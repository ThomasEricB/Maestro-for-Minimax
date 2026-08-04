const path = require('path')
module.exports = {
  version: "8.0",
  title: "Maestro",
  description: "An all-in-one, 100% local AI video, image & music studio. Its Director mode turns a single prompt into a full music video or short film — LLM-planned, shot by shot. Built on the WanGP pipeline (Wan 2.1/2.2, LTX-2.3, Qwen, Hunyuan Video, Flux). Requires an NVIDIA GPU (6GB+ VRAM).",
  icon: "maestro_simplified_icon_alpha.png",
  menu: async (kernel, info) => {
    // Do not gate this menu on kernel.gpu. Pinokio can render an app menu
    // before its hardware inventory has populated that property, which would
    // hide Start from supported systems. install.js retains the documented
    // execution-time NVIDIA check for fresh installations.
    let installed = info.exists("app/env")
    let running = {
      install: info.running("install.js"),
      start: info.running("start.js"),
      start_classic: info.running("start_classic.js"),
      update: info.running("update.js"),
      reset: info.running("reset.js"),
      blackwell: info.running("blackwell.js")
    }
    // Optional: swap the venv for a CUDA 13 + SageAttention 3 build. Opt-in,
    // RTX 50-series only, and it replaces app/env rather than adding a second
    // venv — on Blackwell the default cu128 stack is strictly worse.
    let has = {
      blackwell: info.exists("app/env/.maestro_blackwell_v1.installed")
    }
    if (running.install) {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Installing",
        href: "install.js",
      }]
    } else if (installed) {
      if (running.start) {
        let local = info.local("start.js")
        if (local && local.url) {
          return [{
            default: true,
            icon: "fa-solid fa-rocket",
            text: "Open Web UI",
            href: local.url,
          }, {
            icon: "fa-solid fa-rocket",
            text: "Open Classic UI",
            href: local.url + "/classic",
          }, {
            icon: 'fa-solid fa-terminal',
            text: "Terminal",
            href: "start.js",
          }]
        } else {
          return [{
            icon: 'fa-solid fa-terminal',
            text: "Terminal",
            href: "start.js",
          }]
        }
      } else if (running.start_classic) {
        let local = info.local("start_classic.js")
        if (local && local.url) {
          return [{
            default: true,
            icon: "fa-solid fa-rocket",
            text: "Open Classic UI",
            href: local.url,
          }, {
            icon: 'fa-solid fa-terminal',
            text: "Terminal",
            href: "start_classic.js",
          }]
        } else {
          return [{
            icon: 'fa-solid fa-terminal',
            text: "Terminal",
            href: "start_classic.js",
          }]
        }
      } else if (running.update) {
        return [{
          default: true,
          icon: 'fa-solid fa-terminal',
          text: "Updating",
          href: "update.js",
        }]
      } else if (running.reset) {
        return [{
          default: true,
          icon: 'fa-solid fa-terminal',
          text: "Resetting",
          href: "reset.js",
        }]
      } else if (running.blackwell) {
        return [{
          default: true,
          icon: 'fa-solid fa-terminal',
          text: "Building Blackwell venv",
          href: "blackwell.js",
        }]
      } else {
        return [{
          icon: "fa-solid fa-power-off",
          text: "Start",
          href: "start.js",
        }, {
          icon: "fa-solid fa-display",
          text: "Start (Classic UI)",
          href: "start_classic.js",
        }, {
          icon: "fa-solid fa-power-off",
          text: "Advanced",
          menu: [{
            icon: "fa-solid fa-power-off",
            text: "Compiled (Faster but may not work)",
            href: "start.js",
            params: {
              compile: true
            }
          }, {
            icon: "fa-solid fa-power-off",
            text: "Classic Compiled",
            href: "start_classic.js",
            params: {
              compile: true
            }
          }]
        }, {
          icon: "fa-regular fa-folder-open",
          text: "T2V Loras (save lora files here)",
          href: "app/loras",
          fs: true
        }, {
          icon: "fa-regular fa-folder-open",
          text: "I2V Loras (save lora files here)",
          href: "app/loras_i2v",
          fs: true
        }, {
          icon: "fa-solid fa-plug",
          text: "Update",
          href: "update.js",
        }, {
          icon: "fa-solid fa-plug",
          text: "Install",
          href: "install.js",
        }, {
          // Install / re-install the SAM 3.1 segmentation service
          // (separate Python 3.12 conda env, takes ~5 min). Only
          // needed for the experimental Inpaint feature in Edit
          // mode — most users never need it, which is why install.js
          // no longer runs sam_install.js automatically. Label flips
          // to "Update Inpaint Support" once installed so users can
          // refresh SAM independently of the main app update.
          icon: "fa-solid fa-vector-square",
          text: info.exists("app/services/sam/env")
            ? "Update Inpaint Support (SAM 3.1)"
            : "Install Inpaint Support (SAM 3.1)",
          href: "sam_install.js",
        }, {
          // One-shot venv swap: CUDA 13 + SageAttention 3 + flash-attn +
          // xformers + torchao. RTX 50-series (Linux) only; it REPLACES
          // app/env rather than adding a second venv.
          icon: "fa-solid fa-bolt",
          text: has.blackwell
            ? "[Linux] Blackwell Turbo Install (active)"
            : "[Linux] Blackwell Turbo Install",
          href: "blackwell.js",
          mode: "refresh",
          confirm: has.blackwell
            ? "Rebuild the Blackwell venv from scratch (CUDA 13 + SageAttention 3 + flash-attn + xformers). ~20-30 minutes of compiling."
            : "REPLACES Maestro's venv with a CUDA 13.0 build carrying SageAttention 3 (2.4-3.3x on long sequences), flash-attn 2.8.3, xformers, and torchao. SageAttention 2 is dropped — no CUDA 13 wheel exists — and sage3 replaces it. ~10GB download plus 20-30 minutes of compiling. The new venv is fully verified before your current one is removed, and Reset + Install always returns you to the default stack. RTX 50-series + Linux only."
        }, {
          icon: "fa-regular fa-circle-xmark",
          text: "<div><strong>Reset</strong><div>Revert to pre-install state</div></div>",
          href: "reset.js",
          confirm: "Are you sure you wish to reset the app?"
        }]
      }
    } else {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Install",
        href: "install.js",
      }]
    }
  }
}
