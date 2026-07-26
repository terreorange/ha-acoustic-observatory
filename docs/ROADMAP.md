# Feuille de route

## v0.1 - Connectivité

- [x] Wi-Fi
- [x] Mosquitto
- [x] MQTT Discovery
- [x] compteur de test
- [ ] disponibilité fiable après reconnexion

## v0.2 - Acquisition audio

- [ ] initialiser le microphone intégré
- [ ] lire des blocs d'échantillons
- [ ] afficher min, max, moyenne et RMS dans le terminal
- [ ] publier un niveau relatif dans Home Assistant

## v0.3 - Analyse fréquentielle

- [ ] ajouter une bibliothèque FFT
- [ ] supprimer l'offset continu
- [ ] appliquer une fenêtre
- [ ] calculer la fréquence dominante
- [ ] publier les bandes 40-80 Hz et 80-160 Hz

## v0.4 - Détection

- [ ] définir un score de bourdonnement
- [ ] distinguer bruit ambiant et ronronnement continu
- [ ] créer un binary_sensor Home Assistant
- [ ] mesurer la durée cumulée

## v0.5 - Exploitation

- [ ] tableau de bord Home Assistant
- [ ] statistiques horaires et quotidiennes
- [ ] corrélation avec météo et température
- [ ] export des données

## v1.0 - Station stable

- [ ] fonctionnement continu
- [ ] mise à jour OTA
- [ ] documentation d'installation
- [ ] procédure de calibration relative
