# Sandbox setup

Sandboxing agent tools is recommended but optional. This document describes
the sandbox option; native macOS build and browser setup is in
[README.md](README.md#agent-section-for-setup) (the original assignment brief in this folder). Docker is not required to
build the port or serve its generated files.

If you choose a sandbox:

- Mount only this repo into the sandbox.
- Use the `Dockerfile` here as the toolchain image (Emscripten included).
- Network policy: deny by default. Allow only what you decide you need,
  for example the Emscripten docs. Decide this on purpose and write down
  why. Existing browser ports of Doom are on the open web; this project
  is you building yours.
- Credentials: none should be present in the sandbox.

How you configure this depends on your sandbox tool. Writing the
config file for it is part of your Day 1 work. Keep student-created sandbox
policies and configuration in `loop/`, and save run logs in `artifacts/`.
The shared toolchain `Dockerfile` stays at the repository root.

## Supplied toolchain

The Dockerfile pins Emscripten 4.0.18 by image digest and Playwright 1.56.1,
including its matching Chromium download. Building the image needs network
access for the base image, OS packages, and browser tooling. Those downloads
are separate from the runtime network policy. OS package repositories are
not snapshot-pinned; record the built image ID for the environment you use.

Build from the repository root with `docker build -t doom-capstone-toolchain .`.
The image build checks the compiler and launches/closes headless Chromium;
this does not verify the game. Docker must be running before building.

In the image, CommonJS scripts can use `require('playwright')`. ESM scripts
can import `/opt/browser-tools/lib/node_modules/playwright/index.mjs`.
`PLAYWRIGHT_BROWSERS_PATH` points to the preinstalled browser binaries.
The image supplies tools; it does not by itself enforce runtime network or
file permissions.
