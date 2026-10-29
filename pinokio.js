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
      prompt_studio: info.running("prompt_studio.js")
    }
    // The H3 prompt studio drives Maestro's own LLM endpoints. Maestro's port is assigned at launch, so
    // the URL is handed over as a param rather than guessed.
    let studio = running.prompt_studio ? info.local("prompt_studio.js") : null
    let studioUp = !!(studio && studio.url)
    // Pinokio auto-selects whichever item carries `default`, and re-renders this menu every time a
    // script's state changes. While `default` sat on Maestro's Web UI, launching the studio re-rendered
    // the menu and the selection snapped straight back to Maestro — the studio tab was present, just
    // never selected, which looks exactly like the button "opening Maestro". So the default follows the
    // studio while it is up, and returns to Maestro once it stops.
    let studioMenu = (maestroUrl) => {
      if (studioUp) {
        return [{
          default: true,
          icon: "fa-solid fa-comment-dots",
          text: "Open H3 Prompt Studio",
          href: studio.url,
        }]
      }
      return [{
        icon: "fa-solid fa-comment-dots",
        text: running.prompt_studio ? "Starting H3 Prompt Studio" : "H3 Prompt Studio",
        href: "prompt_studio.js",
        // Only pass an address when there is one. A `null` here reaches the script as the *string*
        // "null", which is truthy, so it defeated the fallback and became the studio's Maestro address.
        params: maestroUrl ? { maestro: maestroUrl } : {}
      }]
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
            // Yields the default to the studio while that is open, so starting it does not bounce the
            // view straight back here.
            default: !studioUp,
            icon: "fa-solid fa-rocket",
            text: "Open Web UI",
            href: local.url,
          }, {
            icon: "fa-solid fa-rocket",
            text: "Open Classic UI",
            href: local.url + "/classic",
          }, ...studioMenu(local.url), {
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
      } else {
        return [{
          icon: "fa-solid fa-power-off",
          text: "Start",
          href: "start.js",
        }, {
          icon: "fa-solid fa-display",
          text: "Start (Classic UI)",
          href: "start_classic.js",
        }, ...studioMenu(null), {
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
