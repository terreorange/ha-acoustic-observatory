# Architecture

## Principe

```text
Microphone Atom Echo
        |
        v
Acquisition audio I2S
        |
        v
Prétraitement et FFT
        |
        v
Extraction d'indicateurs
        |
        v
Publication MQTT
        |
        v
Mosquitto + Home Assistant
```

## Composants prévus

### Connectivité

- Wi-Fi avec reconnexion automatique ;
- MQTT avec message de disponibilité ;
- publication conservée des états utiles ;
- découverte automatique Home Assistant.

### Audio

- acquisition du microphone intégré ;
- suppression de la composante continue ;
- fenêtrage ;
- FFT ;
- calcul de l'énergie par bandes.

### Indicateurs

- niveau audio relatif ;
- fréquence dominante ;
- énergie 40-80 Hz ;
- énergie 80-160 Hz ;
- score de bourdonnement ;
- état binaire de détection.

## Décisions

- PlatformIO et framework Arduino pour accélérer le prototypage ;
- MQTT pour découpler le capteur de Home Assistant ;
- aucune promesse de mesure réglementaire sans calibration et microphone adapté ;
- priorité à la stabilité 24 h/24 et à la tendance dans le temps.
