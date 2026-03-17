![OpenToys Cover](assets/cover.png)

<div align="center">
<div style="display:flex; flex-direction:row; align-items:center; flex-wrap:wrap; justify-content:center;">
  <!-- <a style="display:inline-flex;" href="https://cookbook.openai.com/examples/voice_solutions/running_realtime_api_speech_on_esp32_arduino_edge_runtime_elatoai"><img src="assets/oai.png" height="42" style="width: auto;"></a>   -->
  <a style="display:inline-flex;" href="https://www.elatoai.com/docs"><img src="assets/docs.png" height="42" style="width: auto;"></a>
  <a style="display:inline-flex;" href="https://discord.gg/KJWxDPBRUj"><img src="assets/discord.png" height="42" style="width: auto;"></a>
<!-- <a style="display:inline-flex;" href="https://elatoai.com/home"><img src="assets/try.png" height="42" style="width: auto;"></a> -->
      <!-- <a style="display:inline-flex;" href="https://www.kickstarter.com/projects/elatoai/elato-make-toys-talk-with-ai-voices"><img src="assets/ks.png" height="42" style="width: auto;"></a> -->
 <a style="display:inline-flex;" href="https://www.elatoai.com/products"><img src="assets/diy.png" height="42" style="width: auto;"></a>
</div>
</div>

# 🎥 Demo Video

[![OpenToys Demo](assets/demo.png)](https://www.youtube.com/watch?v=YTIHN36Lb7M)

# 👾 OpenToys 

Build your own Local AI Toys, Desk Companions, Robots and more with an ESP32. OpenToys enables multilingual realtime speech with voice cloning andruns fully on-device on Apple Silicon without sending your data to the cloud.

## Why OpenToys?

- **Fully Local**: NO cloud, NO subscriptions, NO data leaving your home, FREE AI forever.
- **Multilingual**: OpenToys supports multiple languages and accents: English 🇺🇸/🇬🇧, Chinese 🇨🇳, Spanish 🇪🇸, French 🇫🇷, Japanese 🇯🇵, Korean 🇰🇷, Portuguese 🇵🇹, German 🇩🇪, Italian 🇮🇹 and more!
- **Voice Cloning**: Clone your own voice or your favorite characters with <10s of audio.
- **Customizable**: Build your own toys, companions, robots and more with an ESP32.
- **Open-source**: The community is open-source and free to use and contribute to.

## 🚀 Quick Start

1. Clone the repository with `git clone https://github.com/akdeb/open-toys.git`
2. Install Rust and Tauri with `curl https://sh.rustup.rs -sSf | sh`
3. Install Node from [here](https://nodejs.org/en/download)
4. Run `cd app`
5. Run `npm install`
6. Run `npm run tauri dev`

## ESP32 DIY Hardware

![ESP32 DIY Hardware](assets/pcb-design.png)
[Firmware Docs ⏭️](https://www.elatoai.com/docs/blog/firmware)


## App Design

![App Design](assets/app.png)

## Cards & Stories
Create experiences with personalities that can play games, tell stories, engage in educational conversations. Here are some example characters: Math Bear, Cosmo the Monkey, Bio Shark, Coach Carter and more!

<p align="center">
<img src="assets/card1.png" height="280">
<img src="assets/card2.png" height="280">
<img src="assets/card3.png" height="280">
<img src="assets/card4.png" height="280">
</p>

## Stack

- STT: Whisper Turbo ASR
- TTS: Qwen3-TTS and Chatterbox-turbo
- LLMs: any LLM from [`mlx-community`](http://huggingface.co/mlx-community) (Qwen3, Llama, Mistral3, etc.)
- App: Tauri, React, Tailwind CSS, TypeScript, Rust
- Platform focus: Apple Silicon (M1/2/3/4/5)
- Hardware device: ESP32-S3

## Download

- Direct DMG: [OpenToys_0.1.0_aarch64.dmg](https://github.com/akdeb/open-toys/releases/latest/download/OpenToys_0.1.0_aarch64.dmg)
- All releases: [GitHub Releases](https://github.com/akdeb/open-toys/releases)

## ⚡️ Flash to ESP32

1. Connect your ESP32-S3 to your Apple Silicon Mac.
2. In OpenToys, go to `Settings` and click `Flash Firmware`.
3. OpenToys flashes bundled firmware images (`bootloader`, `partitions`, `firmware`) directly.
4. After flashing, the toy opens a WiFi captive portal (`ELATO`) for network setup.
5. Put your Mac and toy on the same WiFi network; the toy reconnects when powered on while OpenToys is running.

## 🛡️ Safety Considerations

AI systems (local or cloud) are not perfect. This project is built with data privacy and safety in mind, but there are still important limitations:

- **Hallucinations**: LLM and TTS models can give incorrect or misleading answers. This should not be treated as a source of truth.
- **Inappropriate outputs**: Adversarial or ambiguous prompts can sometimes produce unsafe responses.
- **Emotional impact**: AI should not replace real human interaction, especially for children.

*When using with children, use with parental awareness and treat this as a tool for exploration, not authority.*

## Tested on ✅

1. M1 Pro 2021 Macbook Pro
2. M3 2024 Macbook Air
2. M4 Pro 2024 Macbook Pro

## Project Structure

```
open-toys/
├── app/
├── arduino/
├── resources/
├────────── python-backend/
├────────── firmware/
└── README.md
```

Python 3.11 runtime binary, packages and HF models are downloaded on first app setup into the app data directory.

## License
MIT

---

If you like this project, consider supporting us with a star ⭐️ on GitHub!
