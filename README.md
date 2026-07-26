# Atom Echo Noise

Capteur acoustique expérimental basé sur un M5Stack Atom Echo, destiné à suivre un bourdonnement grave et à publier les mesures dans Home Assistant via MQTT.

## État actuel

La version initiale valide toute la chaîne :

1. connexion Wi-Fi ;
2. connexion au broker Mosquitto ;
3. publication MQTT ;
4. découverte automatique dans Home Assistant ;
5. publication d'un compteur de test.

## Installation

1. Copier `include/secrets.example.h` vers `include/secrets.h`.
2. Renseigner le Wi-Fi et les identifiants MQTT.
3. Ouvrir le dossier dans VS Code avec PlatformIO.
4. Téléverser le firmware.
5. Ouvrir le moniteur série à 115200 bauds.

## Vérification

Home Assistant doit découvrir un appareil nommé **Atom Echo bruit** avec une entité **Compteur**.

## Objectif final

Le compteur sera progressivement remplacé par :

- un niveau audio relatif ;
- l'énergie dans les bandes basses fréquences ;
- la fréquence dominante ;
- un indicateur de bourdonnement ;
- un historique exploitable dans Home Assistant.

Ce projet ne vise pas à produire une mesure acoustique réglementaire en dB(A).
