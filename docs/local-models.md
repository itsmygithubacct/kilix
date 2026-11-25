# Local models with `kilix llama`

`kilix llama` runs kilix-llm through Kilix. The first use installs the pinned
kilix-llm, and it installs it again whenever this checkout's pin moves; the
first chat, run or serve then builds llama.cpp. Its commands:

| Command | What it does |
|---|---|
| `kilix llama chat` | talk to a local model (`run 'PROMPT'` for one answer, `serve` for a loopback OpenAI-compatible server) |
| `kilix llama status` | which llama.cpp build is in use, and what rollback returns to |
| `kilix llama update [--check]` | build llama.cpp's newest release for the backends in use and switch to it, after it starts and accepts every command line kilix-llm uses |
| `kilix llama rollback` | switch back to the build before the last update |
| `kilix llama install` | install and build now rather than on first use: CPU, plus Vulkan when there is a GPU and `glslc` |

The same actions are in the GUI Start menu, under Software ▸ Kilix
components ▸ Local models (llama.cpp), and in Kilix 95, under Programs ▸ Local
Models (llama.cpp); right-click that entry for the others. `kilix install`
lists the runtime as `llama-cpp`, and `kilix install --update llama-cpp` is the
updater. Building needs a C++ compiler, CMake and rsync, plus `glslc` and the
Vulkan headers for the GPU backend; OpenBLAS speeds up the CPU backend.
Plebian-OS installs all of them with the desktop, and `kilix llama` names any
that are missing. `kilix deps` reinstalls Kilix's own build prerequisites.
