# Acoustic Observatory Add-on

This directory contains the future Home Assistant add-on for HA Acoustic Observatory.

The target add-on will:

- subscribe to `atom_echo_noise/spectrum/state`;
- store spectrum history locally;
- provide an Ingress web UI inside Home Assistant;
- display the current spectrum and a waterfall;
- let users label measurement sessions;
- publish higher-level detection entities back to Home Assistant.

## Status

Foundation only. The add-on skeleton is included so the repository layout is ready, but it still needs validation on Home Assistant OS before being considered stable.
