// Wipes every install artifact so the next Install starts from scratch.
// Mirrors the directories created by install.js and sam_install.js.
module.exports = {
  run: [
    // Main Python venv
    { method: "fs.rm", params: { path: "app/env" } },
    // SAM 3.1 Python 3.12 conda env
    { method: "fs.rm", params: { path: "app/services/sam/env" } },
    // SAM 3 source checkout (will be re-cloned on install)
    { method: "fs.rm", params: { path: "app/services/sam/sam3" } },
    // UI build artifacts
    { method: "fs.rm", params: { path: "ui/node_modules" } },
    { method: "fs.rm", params: { path: "ui/dist" } },
    // Blackwell venv build artifacts (blackwell.js): the private CUDA 13
    // toolkit and the two source checkouts it compiles. The venv itself went
    // with app/env above, which also takes its .maestro_blackwell marker — so
    // the next Install returns to the default cu128 stack.
    { method: "fs.rm", params: { path: "cuda13-toolkit" } },
    { method: "fs.rm", params: { path: "SageAttention" } },
    { method: "fs.rm", params: { path: "xformers_src" } },
    // A build that failed before the swap leaves this behind.
    { method: "fs.rm", params: { path: "app/env_blackwell" } }
  ]
}
