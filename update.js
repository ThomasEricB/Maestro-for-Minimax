const {
  runtimeProfile,
} = require("./launcher_profile")

module.exports = async (kernel) => {
  const runtime = runtimeProfile(kernel)
  const alreadyCurrentAndReady =
    `{{/already up[- ]to[- ]date|is up to date/i.test(input.stdout) && exists('${runtime.marker}') && exists('${runtime.flashMarker}') ? 'uptodate' : 'build'}}`
  return {
    run: [{
    // Pull the latest launcher + app code (single monorepo, so this one
    // pull covers both `ui/` and `app/`). The NEXT step inspects this
    // pull's output: if the repo was already current, there is nothing
    // new to install or rebuild, so we skip straight to the end instead
    // of spending several minutes on a redundant dependency install +
    // UI build.
    //
    // --rebase, not a plain merge: this install carries local fixes
    // committed on top of upstream (the Vulkan llama.cpp asset, fp8-only
    // H3 pinning, expandable CUDA segments, the inference_mode save fix
    // and the llama.cpp SONAME repair). A merge would bury them under
    // merge commits and let an upstream edit to the same lines quietly
    // win. Rebasing replays them onto each new release so they keep
    // applying -- and if one ever stops applying, the rebase stops
    // loudly instead of dropping the fix on the floor.
    //
    // --autostash so an uncommitted experiment in the tree cannot block
    // the update; it is reapplied once the rebase lands.
    //
    // `|| git rebase --abort` keeps a conflicted rebase from stranding
    // the repo mid-rebase: the tree is restored exactly as it was and the
    // conflict stays visible in the terminal to resolve by hand.
    method: "shell.run",
    params: {
      message: "git pull --rebase --autostash || git rebase --abort"
    }
  }, {
    // Branch on the git pull output (captured here as input.stdout — a
    // shell.run always returns its raw terminal content as stdout):
    //   - already current  -> jump to "uptodate" (log a notice, then end)
    //   - new commits found -> jump to "build"   (run the full update)
    // Matches the merge wording "Already up to date", the older git
    // "Already up-to-date" spelling, and "Current branch <name> is up to
    // date" — which is what `git pull --rebase` prints instead — all
    // case-insensitively. If detection
    // ever fails (e.g. empty stdout), the regex simply won't match and
    // we fall through to "build" — the safe default is a full update,
    // never a wrongly-skipped rebuild.
    method: "jump",
    params: {
      // An already-current checkout still enters the build path when either
      // its hardware runtime or optional FlashAttention repair marker is
      // missing. This keeps interrupted installs and one-time repairs resumable.
      id: alreadyCurrentAndReady
    }
  }, {
    // Reached ONLY when the repo was already current (the "build" path
    // jumps over this step). Before halting, self-heal the seed-vc
    // component if it's missing (GPL-3.0, cloned from its own repo — see
    // install.js): a failed earlier clone shouldn't leave voice features
    // broken until the next code update.
    id: "uptodate",
    when: "{{!exists('app/postprocessing/seedvc/__init__.py')}}",
    method: "shell.run",
    params: {
      message: "git clone --depth 1 --branch v1.0.0 https://github.com/Blizaine/maestro-seedvc app/postprocessing/seedvc"
    }
  }, {
    method: "log",
    params: {
      raw: "Already up to date — no new commits pulled. Skipped dependency install and UI rebuild."
    },
    next: null
  }, {
    // Fetch the seed-vc component if missing (GPL-3.0, own repository —
    // see install.js). Runs at the top of the build path so the update
    // that removed the formerly-tracked tree clones it right back, and
    // any later update self-heals a failed clone. Keep the pinned tag in
    // sync with install.js.
    id: "build",
    when: "{{!exists('app/postprocessing/seedvc/__init__.py')}}",
    method: "shell.run",
    params: {
      message: "git clone --depth 1 --branch v1.0.0 https://github.com/Blizaine/maestro-seedvc app/postprocessing/seedvc"
    }
  }, {
    method: "shell.run",
    params: {
      venv: runtime.env,
      venv_python: runtime.python,
      path: "app",
      message: "uv pip install -r requirements.txt"
    }
  }, {
    // Existing installs may have the main runtime marker but still contain a
    // Windows FlashAttention wheel whose CUDA DLL cannot load. Repair only
    // that optional wheel once; a normal torch.js run writes both markers.
    when: `{{exists('${runtime.marker}') && !exists('${runtime.flashMarker}')}}`,
    method: "script.start",
    params: {
      uri: "torch.js",
      params: {
        venv: runtime.env,
        path: "app",
        flash_only: true
      }
    }
  }, {
    // Skip the full torch.js path when its main runtime marker is present.
    // FlashAttention has a separate marker and targeted repair step above,
    // so an optional DLL problem never forces a multi-gigabyte Torch reinstall.
    // Saves ~60-120s + ~3 GB of redundant downloads on routine updates.
    //
    // Each hardware profile owns its marker. Bumping the marker in
    // launcher_profile.js makes this gate reinstall that profile on Update;
    // old markers remain harmless until Reset removes the environment.
    //
    // Recovery path: if torch ever ends up in a broken state (e.g. CPU
    // wheel installed where CUDA is expected) AND the marker is still
    // present, RTX 50 users can choose Advanced > Repair RTX 50 Runtime;
    // any user can delete their profile marker and re-run Update, or run
    // Reset for a clean slate.
    when: `{{!exists('${runtime.marker}')}}`,
    method: "script.start",
    params: {
      uri: "torch.js",
      params: {
        venv: runtime.env,
        path: "app",
        xformers: true
      }
    }
  }, {
    // Mirror of the install.js GGUF-kernels step — idempotent, so
    // re-runs cheaply on every update. Catches existing installs
    // up to the new behavior without forcing a reinstall.
    method: "shell.run",
    params: {
      venv: runtime.env,
      venv_python: runtime.python,
      path: "app",
      message: "python scripts/install_gguf_kernels.py"
    }
  }, {
    when: "{{exists('ui/package.json')}}",
    method: "shell.run",
    params: {
      path: "ui",
      message: [
        "npm install",
        "npm run build"
      ]
    }
  },
  // Update SAM 3.1 service (pull latest + reinstall) ONLY if SAM is
  // already installed. This way:
  //   - Users who never installed SAM (most users) don't get a slow
  //     conda env install they didn't ask for during a regular update.
  //   - Users who DID install SAM keep getting it kept up to date
  //     alongside the main app on every update.
  // Fresh-install path: install.js no longer runs sam_install.js;
  // users who want inpaint click "Install Inpaint Support" from the
  // Pinokio menu, which fires sam_install.js once. After that, this
  // gate is satisfied and SAM updates with every regular update.
  {
    when: "{{exists('app/services/sam/env')}}",
    method: "script.start",
    params: {
      uri: "sam_install.js"
    }
    }]
  }
}
