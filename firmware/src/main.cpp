#include <Arduino.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <arduinoFFT.h>
#include <driver/i2s.h>
#include <math.h>

#include "secrets.h"

namespace {

// -----------------------------------------------------------------------------
// Wi-Fi et MQTT
// -----------------------------------------------------------------------------

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

constexpr char kClientId[] = "atom-echo-noise";
constexpr char kAvailabilityTopic[] = "atom_echo_noise/status";

constexpr unsigned long kAnalysisIntervalMs = 5000;
unsigned long lastAnalysisMs = 0;

// -----------------------------------------------------------------------------
// Microphone de l'Atom Echo
// -----------------------------------------------------------------------------

constexpr i2s_port_t kI2sPort = I2S_NUM_0;

constexpr int kI2sBclkPin = 19;
constexpr int kI2sLrclkPin = 33;
constexpr int kI2sDataInPin = 23;

constexpr uint16_t kSampleCount = 2048;
constexpr float kSampleRate = 8000.0f;

int16_t audioSamples[kSampleCount];
float realValues[kSampleCount];
float imaginaryValues[kSampleCount];

ArduinoFFT<float> fft(
    realValues,
    imaginaryValues,
    kSampleCount,
    kSampleRate);

// -----------------------------------------------------------------------------
// Normalisation en indices 0-100
// -----------------------------------------------------------------------------
//
// Ces bornes sont provisoires et devront être ajustées après quelques mesures
// réelles dans différents contextes.
//
// kIndexFloor : niveau considéré comme quasiment nul.
// kIndexCeiling : niveau considéré comme maximal.
//
// La conversion est logarithmique, ce qui est plus adapté à un signal audio
// qu'une simple règle de trois.
//

constexpr float kIndexFloor = 100.0f;
constexpr float kIndexCeiling = 50000.0f;

float clampToIndex(float value)
{
  if (value <= kIndexFloor) {
    return 0.0f;
  }

  if (value >= kIndexCeiling) {
    return 100.0f;
  }

  const float numerator =
      log10f(value / kIndexFloor);

  const float denominator =
      log10f(kIndexCeiling / kIndexFloor);

  if (denominator <= 0.0f) {
    return 0.0f;
  }

  float index =
      100.0f * numerator / denominator;

  if (index < 0.0f) {
    index = 0.0f;
  }

  if (index > 100.0f) {
    index = 100.0f;
  }

  return index;
}

// -----------------------------------------------------------------------------
// Résultats d'analyse
// -----------------------------------------------------------------------------

struct SpectrumResult {
  float rms;

  float band40To63Raw;
  float band63To100Raw;
  float band100To160Raw;
  float band160To250Raw;

  float band40To63Index;
  float band63To100Index;
  float band100To160Index;
  float band160To250Index;

  float lowFrequencyEnergyRaw;
  float lowFrequencyIndex;

  float referenceEnergyRaw;
  float lowFrequencyShare;

  float dominantFrequency;
  float dominantMagnitudeRaw;
  float dominantMagnitudeIndex;
};

// -----------------------------------------------------------------------------
// Entités Home Assistant
// -----------------------------------------------------------------------------

struct SensorDefinition {
  const char* objectId;
  const char* name;
  const char* stateTopic;
  const char* unit;
  const char* icon;
};

constexpr SensorDefinition kSensors[] = {
    {
        "audio_rms",
        "Niveau sonore brut",
        "atom_echo_noise/audio_rms/state",
        "RMS",
        "mdi:waveform",
    },
    {
        "band_40_63",
        "Indice 40-63 Hz",
        "atom_echo_noise/band_40_63/state",
        "/100",
        "mdi:sine-wave",
    },
    {
        "band_63_100",
        "Indice 63-100 Hz",
        "atom_echo_noise/band_63_100/state",
        "/100",
        "mdi:sine-wave",
    },
    {
        "band_100_160",
        "Indice 100-160 Hz",
        "atom_echo_noise/band_100_160/state",
        "/100",
        "mdi:sine-wave",
    },
    {
        "band_160_250",
        "Indice 160-250 Hz",
        "atom_echo_noise/band_160_250/state",
        "/100",
        "mdi:sine-wave",
    },
    {
        "low_frequency_energy",
        "Indice grave total",
        "atom_echo_noise/low_frequency_energy/state",
        "/100",
        "mdi:chart-bell-curve",
    },
    {
        "low_frequency_share",
        "Part des frequences graves",
        "atom_echo_noise/low_frequency_share/state",
        "%",
        "mdi:percent",
    },
    {
        "dominant_frequency",
        "Frequence grave dominante",
        "atom_echo_noise/dominant_frequency/state",
        "Hz",
        "mdi:waveform",
    },
    {
        "dominant_magnitude",
        "Indice de la frequence dominante",
        "atom_echo_noise/dominant_magnitude/state",
        "/100",
        "mdi:signal",
    },
};

// -----------------------------------------------------------------------------
// Connexion Wi-Fi
// -----------------------------------------------------------------------------

void connectWifi()
{
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.printf("Connexion au Wi-Fi : %s\n", WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
  }

  Serial.println();
  Serial.println("Wi-Fi connecte");
  Serial.print("Adresse IP : ");
  Serial.println(WiFi.localIP());
}

// -----------------------------------------------------------------------------
// Découverte Home Assistant
// -----------------------------------------------------------------------------

bool publishSensorDiscovery(const SensorDefinition& sensor)
{
  char discoveryTopic[160];

  snprintf(
      discoveryTopic,
      sizeof(discoveryTopic),
      "homeassistant/sensor/atom_echo_noise/%s/config",
      sensor.objectId);

  char uniqueId[120];

  snprintf(
      uniqueId,
      sizeof(uniqueId),
      "atom_echo_noise_%s",
      sensor.objectId);

  char payload[1024];

  snprintf(
      payload,
      sizeof(payload),
      R"json({
        "name":"%s",
        "unique_id":"%s",
        "state_topic":"%s",
        "availability_topic":"atom_echo_noise/status",
        "payload_available":"online",
        "payload_not_available":"offline",
        "unit_of_measurement":"%s",
        "state_class":"measurement",
        "icon":"%s",
        "device":{
          "identifiers":["atom_echo_noise"],
          "name":"Atom Echo bruit",
          "manufacturer":"M5Stack",
          "model":"Atom Echo"
        }
      })json",
      sensor.name,
      uniqueId,
      sensor.stateTopic,
      sensor.unit,
      sensor.icon);

  const bool published =
      mqttClient.publish(discoveryTopic, payload, true);

  Serial.printf(
      "Decouverte %-35s : %s\n",
      sensor.name,
      published ? "OK" : "ECHEC");

  return published;
}

void publishAllDiscoveryMessages()
{
  for (const SensorDefinition& sensor : kSensors) {
    publishSensorDiscovery(sensor);
  }
}

// -----------------------------------------------------------------------------
// Connexion MQTT
// -----------------------------------------------------------------------------

void connectMqtt()
{
  while (!mqttClient.connected()) {
    Serial.println("Connexion a MQTT...");

    const bool connected = mqttClient.connect(
        kClientId,
        MQTT_USER,
        MQTT_PASSWORD,
        kAvailabilityTopic,
        1,
        true,
        "offline");

    if (connected) {
      Serial.println("MQTT connecte");

      mqttClient.publish(
          kAvailabilityTopic,
          "online",
          true);

      publishAllDiscoveryMessages();
    } else {
      Serial.printf(
          "Echec MQTT, code : %d\n",
          mqttClient.state());

      delay(5000);
    }
  }
}

// -----------------------------------------------------------------------------
// Initialisation du microphone
// -----------------------------------------------------------------------------

bool initializeMicrophone()
{
  const i2s_config_t config = {
      .mode = static_cast<i2s_mode_t>(
          I2S_MODE_MASTER |
          I2S_MODE_RX |
          I2S_MODE_PDM),
      .sample_rate = static_cast<int>(kSampleRate),
      .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = 256,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0,
  };

  const i2s_pin_config_t pins = {
      .bck_io_num = kI2sBclkPin,
      .ws_io_num = kI2sLrclkPin,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = kI2sDataInPin,
  };

  esp_err_t result =
      i2s_driver_install(kI2sPort, &config, 0, nullptr);

  if (result != ESP_OK) {
    Serial.printf(
        "Erreur installation I2S : %d\n",
        result);
    return false;
  }

  result = i2s_set_pin(kI2sPort, &pins);

  if (result != ESP_OK) {
    Serial.printf(
        "Erreur configuration I2S : %d\n",
        result);

    i2s_driver_uninstall(kI2sPort);
    return false;
  }

  i2s_zero_dma_buffer(kI2sPort);

  Serial.println("Microphone initialise");
  return true;
}

// -----------------------------------------------------------------------------
// Acquisition audio
// -----------------------------------------------------------------------------

bool acquireSamples(float& rms)
{
  size_t bytesRead = 0;

  const esp_err_t result = i2s_read(
      kI2sPort,
      audioSamples,
      sizeof(audioSamples),
      &bytesRead,
      portMAX_DELAY);

  if (result != ESP_OK) {
    Serial.printf(
        "Erreur lecture audio : %d\n",
        result);
    return false;
  }

  const size_t samplesRead =
      bytesRead / sizeof(int16_t);

  if (samplesRead != kSampleCount) {
    Serial.printf(
        "Nombre d'echantillons incorrect : %u/%u\n",
        static_cast<unsigned>(samplesRead),
        kSampleCount);
    return false;
  }

  double mean = 0.0;

  for (size_t index = 0; index < kSampleCount; ++index) {
    mean += audioSamples[index];
  }

  mean /= kSampleCount;

  double sumSquares = 0.0;

  for (size_t index = 0; index < kSampleCount; ++index) {
    const float centered =
        static_cast<float>(audioSamples[index] - mean);

    realValues[index] = centered;
    imaginaryValues[index] = 0.0f;

    sumSquares +=
        static_cast<double>(centered) *
        static_cast<double>(centered);
  }

  rms = static_cast<float>(
      sqrt(sumSquares / kSampleCount));

  return true;
}

// -----------------------------------------------------------------------------
// Outils d'analyse spectrale
// -----------------------------------------------------------------------------

size_t frequencyToBin(float frequency)
{
  return static_cast<size_t>(
      roundf(
          frequency *
          static_cast<float>(kSampleCount) /
          kSampleRate));
}

float binToFrequency(size_t bin)
{
  return
      static_cast<float>(bin) *
      kSampleRate /
      static_cast<float>(kSampleCount);
}

double calculateBandPower(
    float minimumFrequency,
    float maximumFrequency)
{
  size_t firstBin = frequencyToBin(minimumFrequency);
  size_t lastBin = frequencyToBin(maximumFrequency);

  const size_t nyquistBin = kSampleCount / 2;

  if (firstBin > nyquistBin) {
    return 0.0;
  }

  if (lastBin > nyquistBin) {
    lastBin = nyquistBin;
  }

  if (lastBin < firstBin) {
    return 0.0;
  }

  double power = 0.0;

  for (size_t bin = firstBin; bin <= lastBin; ++bin) {
    const double magnitude = realValues[bin];
    power += magnitude * magnitude;
  }

  return power;
}

float calculateBandLevel(
    float minimumFrequency,
    float maximumFrequency)
{
  const size_t firstBin = frequencyToBin(minimumFrequency);
  const size_t lastBin = frequencyToBin(maximumFrequency);

  if (lastBin < firstBin) {
    return 0.0f;
  }

  const size_t binCount =
      lastBin - firstBin + 1;

  const double power =
      calculateBandPower(
          minimumFrequency,
          maximumFrequency);

  if (binCount == 0 || power <= 0.0) {
    return 0.0f;
  }

  return static_cast<float>(
      sqrt(power / static_cast<double>(binCount)));
}

// -----------------------------------------------------------------------------
// Analyse FFT
// -----------------------------------------------------------------------------

SpectrumResult analyzeSpectrum()
{
  SpectrumResult result{};

  if (!acquireSamples(result.rms)) {
    return result;
  }

  fft.windowing(
      FFTWindow::Hann,
      FFTDirection::Forward,
      true);

  fft.compute(FFTDirection::Forward);
  fft.complexToMagnitude();

  result.band40To63Raw =
      calculateBandLevel(40.0f, 63.0f);

  result.band63To100Raw =
      calculateBandLevel(63.0f, 100.0f);

  result.band100To160Raw =
      calculateBandLevel(100.0f, 160.0f);

  result.band160To250Raw =
      calculateBandLevel(160.0f, 250.0f);

  result.band40To63Index =
      clampToIndex(result.band40To63Raw);

  result.band63To100Index =
      clampToIndex(result.band63To100Raw);

  result.band100To160Index =
      clampToIndex(result.band100To160Raw);

  result.band160To250Index =
      clampToIndex(result.band160To250Raw);

  const double lowFrequencyPower =
      calculateBandPower(40.0f, 250.0f);

  const double referencePower =
      calculateBandPower(40.0f, 1000.0f);

  result.lowFrequencyEnergyRaw =
      static_cast<float>(
          sqrt(lowFrequencyPower));

  result.referenceEnergyRaw =
      static_cast<float>(
          sqrt(referencePower));

  result.lowFrequencyIndex =
      clampToIndex(result.lowFrequencyEnergyRaw);

  if (referencePower > 0.0) {
    result.lowFrequencyShare =
        static_cast<float>(
            100.0 *
            lowFrequencyPower /
            referencePower);
  } else {
    result.lowFrequencyShare = 0.0f;
  }

  if (result.lowFrequencyShare < 0.0f) {
    result.lowFrequencyShare = 0.0f;
  }

  if (result.lowFrequencyShare > 100.0f) {
    result.lowFrequencyShare = 100.0f;
  }

  const size_t firstLowBin =
      frequencyToBin(40.0f);

  const size_t lastLowBin =
      frequencyToBin(250.0f);

  float maximumMagnitude = 0.0f;
  size_t maximumBin = firstLowBin;

  for (size_t bin = firstLowBin;
       bin <= lastLowBin;
       ++bin) {
    if (realValues[bin] > maximumMagnitude) {
      maximumMagnitude = realValues[bin];
      maximumBin = bin;
    }
  }

  result.dominantFrequency =
      binToFrequency(maximumBin);

  result.dominantMagnitudeRaw =
      maximumMagnitude;

  result.dominantMagnitudeIndex =
      clampToIndex(maximumMagnitude);

  return result;
}

// -----------------------------------------------------------------------------
// Publication MQTT
// -----------------------------------------------------------------------------

bool publishFloat(
    const char* topic,
    float value,
    uint8_t decimals)
{
  char format[12];

  snprintf(
      format,
      sizeof(format),
      "%%.%uf",
      decimals);

  char payload[32];

  snprintf(
      payload,
      sizeof(payload),
      format,
      value);

  return mqttClient.publish(
      topic,
      payload,
      true);
}

void publishSpectrumJson()
{
  char payload[1800];
  size_t position = 0;

  position += snprintf(
      payload + position,
      sizeof(payload) - position,
      "{\"resolution_hz\":%.4f,\"bins\":{",
      kSampleRate / static_cast<float>(kSampleCount));

  bool first = true;

  const size_t firstBin =
      frequencyToBin(20.0f);

  const size_t lastBin =
      frequencyToBin(250.0f);

  for (size_t bin = firstBin;
       bin <= lastBin;
       ++bin) {
    const float frequency =
        binToFrequency(bin);

    const int written = snprintf(
        payload + position,
        sizeof(payload) - position,
        "%s\"%.1f\":%.1f",
        first ? "" : ",",
        frequency,
        realValues[bin]);

    if (written <= 0 ||
        static_cast<size_t>(written) >=
            sizeof(payload) - position) {
      Serial.println(
          "Spectre JSON tronque : buffer insuffisant");
      return;
    }

    position += static_cast<size_t>(written);
    first = false;
  }

  snprintf(
      payload + position,
      sizeof(payload) - position,
      "}}");

  const bool published = mqttClient.publish(
      "atom_echo_noise/spectrum/state",
      payload,
      true);

  Serial.printf(
      "Spectre detaille publie : %s\n",
      published ? "OK" : "ECHEC");
}

void publishSpectrum(const SpectrumResult& result)
{
  publishFloat(
      "atom_echo_noise/audio_rms/state",
      result.rms,
      1);

  publishFloat(
      "atom_echo_noise/band_40_63/state",
      result.band40To63Index,
      1);

  publishFloat(
      "atom_echo_noise/band_63_100/state",
      result.band63To100Index,
      1);

  publishFloat(
      "atom_echo_noise/band_100_160/state",
      result.band100To160Index,
      1);

  publishFloat(
      "atom_echo_noise/band_160_250/state",
      result.band160To250Index,
      1);

  publishFloat(
      "atom_echo_noise/low_frequency_energy/state",
      result.lowFrequencyIndex,
      1);

  publishFloat(
      "atom_echo_noise/low_frequency_share/state",
      result.lowFrequencyShare,
      1);

  publishFloat(
      "atom_echo_noise/dominant_frequency/state",
      result.dominantFrequency,
      1);

  publishFloat(
      "atom_echo_noise/dominant_magnitude/state",
      result.dominantMagnitudeIndex,
      1);

  publishSpectrumJson();

  Serial.println();
  Serial.println("----- Analyse des graves -----");

  Serial.printf(
      "RMS                         : %.1f\n",
      result.rms);

  Serial.printf(
      "40-63 Hz brut / indice      : %.1f / %.1f\n",
      result.band40To63Raw,
      result.band40To63Index);

  Serial.printf(
      "63-100 Hz brut / indice     : %.1f / %.1f\n",
      result.band63To100Raw,
      result.band63To100Index);

  Serial.printf(
      "100-160 Hz brut / indice    : %.1f / %.1f\n",
      result.band100To160Raw,
      result.band100To160Index);

  Serial.printf(
      "160-250 Hz brut / indice    : %.1f / %.1f\n",
      result.band160To250Raw,
      result.band160To250Index);

  Serial.printf(
      "Grave total brut / indice   : %.1f / %.1f\n",
      result.lowFrequencyEnergyRaw,
      result.lowFrequencyIndex);

  Serial.printf(
      "Part du grave               : %.1f %%\n",
      result.lowFrequencyShare);

  Serial.printf(
      "Frequence dominante         : %.1f Hz\n",
      result.dominantFrequency);

  Serial.printf(
      "Dominante brute / indice    : %.1f / %.1f\n",
      result.dominantMagnitudeRaw,
      result.dominantMagnitudeIndex);
}

} // namespace

void setup()
{
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("Demarrage Atom Echo FFT");

  connectWifi();

  mqttClient.setServer(
      MQTT_HOST,
      MQTT_PORT);

  const bool bufferConfigured =
      mqttClient.setBufferSize(4096);

  Serial.printf(
      "Buffer MQTT 4096 octets : %s\n",
      bufferConfigured ? "OK" : "ECHEC");

  if (!initializeMicrophone()) {
    Serial.println(
        "Le microphone n'a pas pu etre initialise");

    while (true) {
      delay(1000);
    }
  }
}

void loop()
{
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }

  if (!mqttClient.connected()) {
    connectMqtt();
  }

  mqttClient.loop();

  if (millis() - lastAnalysisMs <
      kAnalysisIntervalMs) {
    return;
  }

  lastAnalysisMs = millis();

  const SpectrumResult result =
      analyzeSpectrum();

  publishSpectrum(result);
}
