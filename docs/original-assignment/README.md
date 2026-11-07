# Capstone: build an agent loop to port DOOM

**You have one week to design, operate, and evaluate a multi-agent system
that can carry a substantial software task through to a verified result.**

Your workload is porting the supplied Doom engine from C to WebAssembly
with Emscripten so it runs and plays in a browser. You decide what faithful
play means and how to establish it.

The main deliverable is your loop: how it assigns work, challenges claims,
uses evidence, preserves state, recovers from failure, and decides to stop.
A working game is necessary, but it does not establish that your system works.

## Your responsibility

This is a brief for you, the student. You must turn it into an architecture,
role-specific instructions, acceptance criteria, and an evaluation plan.
Passing this README to an ordinary coding agent and submitting its output
does not satisfy the assignment.

You own the system design and the decisions about what evidence to trust.
Agents implement and repair the port. Design the loop yourself; repository
agent instructions prohibit agents from designing it or modifying `loop/`.
You must explain your choices and demonstrate their consequences.
Record manual interventions rather than attributing them to the loop.

## Design decisions you must make

Before implementation, document your answers in `loop/`:

- **Work:** How does a broad goal become bounded tasks? Who decides what
  happens next when agents disagree or progress stalls?
- **Context:** What does each role see? What information must survive when
  every agent starts with a fresh context?
- **Authority:** Which tools and files can each role access? Who can change
  the checks used to accept the coder's work?
- **Evidence:** What constitutes success? How will you distinguish a plausible
  report from a verified result, and a weak check from a useful one?
- **Recovery:** What happens after a tool error, failed check, interrupted
  run, or repeated unsuccessful repair?
- **Limits:** What are the time, token or cost, and retry budgets? How do
  success, exhaustion, and stalled progress produce different stopping decisions?

There is no prescribed orchestration algorithm. Your design should make these
choices explicit and testable.

## Required roles and tools

Use separate **coder, reviewer, and tester** agent invocations, with separate
contexts and explicit handoffs. They may use the same model. Define their
responsibilities, permissions, and the information exchanged between them.
A single agent describing itself as three roles is not sufficient.

Use **MCP for tool access and handoffs** where needed. Include the relevant tool definitions
and configuration. Explain how the tools enforce the boundaries you chose;
role names and prompts alone do not enforce permissions.

Sandboxing is recommended but optional; see [SANDBOX.md](SANDBOX.md). Document
network access and pin your toolchain version. Keep credentials out of the
repository and sandbox.

## Evaluate the system you built

Write your own measurable acceptance criteria before the agents implement
the port. Cover actual use in your chosen browser and explain the limits of
your checks. A successful compile, screenshot, or agent declaration alone
cannot establish that the task is complete.

Your evaluation must demonstrate:

- **Independent verification:** acceptance checks are protected from coder
  edits, and results identify the source revision and supporting evidence.
- **Checks that catch faults:** deliberately broken behavior causes the
  relevant checks to fail. Explain what those checks still cannot establish.
- **Autonomous repair:** after initial success, a teammate or instructor
  introduces at least two regressions without disclosing their cause to the
  repair agents. The loop detects and repairs them and verifies the result.
- **Resumption:** an interrupted run continues with fresh agent contexts
  using saved state, without you reconstructing its history by hand.
- **Accountability:** you can trace findings to subsequent actions and explain
  the resource usage, manual interventions, and final stopping decision.

Preserve failing evidence as well as passing evidence. Record changes to
acceptance criteria separately, with a reason. Discuss disagreements and
missed faults in your write-up.

## Where your work belongs

| Location | Purpose |
| --- | --- |
| [`loop/`](loop/README.md) | All loop code, prompts, role definitions, MCP configuration, and design documentation. |
| [`artifacts/`](artifacts/README.md) | All durable run state, handoffs, logs, review findings, and evidence. Agents must use this folder between runs. |
| `tests/` | Verification code, including checks for the loop itself. |
| `platform/`, `web/` | The port and browser page. |
| `third_party/doomgeneric/` | Supplied engine; platform ports have been removed. |
| `assets/` | Included shareware `doom1.wad` (Episode 1). |
| `build/`, `dist/` | Generated build output. |

Keep the root for project documentation and shared build/toolchain files.
The starter supplies engine source, platform and HTML stubs, the shareware
WAD, documentation, and an optional Docker toolchain. It supplies no working
game, build script, loop implementation, or test suite. `artifacts/` starts
with its README only; students create run evidence there during the project.
Use the supplied engine and Emscripten. Documentation is allowed; copying an
existing Doom browser port does not satisfy the assignment.

See [`platform/README.md`](platform/README.md) for engine integration caveats,
including pixel format, timing, and audio. The supplied game data already
contains sound effects and music; no additional sound assets are required.

## Agent section for setup

### macOS without Docker

Install the build tools directly on your Mac to compile and test the browser
port. Docker is not required for this workflow. There is currently no macOS
setup script; run the steps below manually.

1. **Install prerequisites.** In Terminal, run:

   ```sh
   xcode-select --install
   ```

   Finish the installer, then check `git --version` and `python3 --version`.
   If Python is missing, install it from [python.org](https://www.python.org/downloads/macos/),
   or run `brew install python` if you already have Homebrew.

2. **Install Emscripten.** Use version `4.0.18` to match the supplied Dockerfile:

   ```sh
   mkdir -p ~/Developer
   cd ~/Developer
   git clone https://github.com/emscripten-core/emsdk.git
   cd emsdk
   ./emsdk install 4.0.18
   ./emsdk activate 4.0.18
   source ./emsdk_env.sh
   emcc --version
   ```

   If you already have an SDK checkout at that location, use it instead of
   cloning again. In each new Terminal session where you want to compile, run:

   ```sh
   source ~/Developer/emsdk/emsdk_env.sh
   ```

   See the [official installation instructions](https://emscripten.org/docs/getting_started/downloads.html).

3. **Verify the compiler and browser workflow.** Return to this repository's
   root directory, then run:

   ```sh
   mkdir -p build/smoke
   cat > build/smoke/hello.c <<'EOF'
   #include <stdio.h>
   int main(void) {
       printf("Emscripten works!\n");
       return 0;
   }
   EOF

   emcc build/smoke/hello.c -o build/smoke/index.html
   python3 -m http.server 8000 --bind 127.0.0.1 --directory build/smoke
   ```

   Open <http://localhost:8000>. The generated page should print
   `Emscripten works!`. Press **Control-C** in Terminal to stop the server.
   This verifies the toolchain and local serving, not the game.

4. **Have your agents implement and build the game.** The supplied platform callbacks are
   empty, there is no entry point or build script, and the HTML does not yet
   load a compiled module. These are student implementation tasks; see
   [platform/README.md](platform/README.md). Document the actual build command
   once implemented. A completed build could produce:

   ```text
   dist/
     index.html
     doom.js
     doom.wasm
     doom.data
   ```

   The exact filenames depend on the build configuration. For example,
   `doom.data` could contain the packaged shareware WAD. The build must make
   that WAD available to the engine inside the browser module.

5. **Test the completed game locally.** Once the build output is in `dist/`,
   run this from the repository root:

   ```sh
   python3 -m http.server 8000 --bind 127.0.0.1 --directory dist
   ```

   Open <http://localhost:8000> and exercise the game's behavior. Use a local
   web server rather than opening the HTML file directly, as explained in
   [Emscripten's deployment documentation](https://emscripten.org/docs/compiling/Deploying-Pages.html).

6. **Publish to a personal website.** Upload the contents of `dist/` to a
   directory such as `/doom/` on your static host, preserving relative paths.
   Visit `https://your-domain.com/doom/` and verify the deployed game. Ensure
   the host serves `.wasm` files with the `application/wasm` content type.
   Builds using shared-memory threads also need cross-origin isolation
   headers. Visitors need only a compatible browser; Emscripten is a build
   tool installed on the developer's machine.

### How to run the loop

There is no runnable loop in the starter. `loop/loop.sh` contains only shell
setup, and `loop/agents.yaml` is empty. Running the script currently does no
work. Students must implement and document their own entry point and
dependencies before running it.

If your completed implementation uses `loop/loop.sh`, run it from the
repository root:

```sh
chmod +x ./loop/loop.sh
./loop/loop.sh
```

## What to hand in

- Your loop implementation and design rationale in `loop/`, with instructions
  for setup, starting, and resuming a run.
- The resulting port, its build/run instructions, and readable Git history.
- Your acceptance criteria, verification code, and evidence for the final revision.
- Artifacts demonstrating review, failed checks, repair, resumption, resource
  usage, and the stopping decision.
- A short reflection: what your architecture helped catch, what it missed,
  and which design choice you would change based on the evidence.

Be prepared to explain why each role and handoff earns its place in the system.

## License and game data

Keep the engine's license notices; see [LICENSE](LICENSE). Use only the
shareware `doom1.wad` for this assignment. Do not add commercial WADs.
