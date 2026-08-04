// OPTIONAL: replace Maestro's venv with a Blackwell-optimised one.
//
// RTX 50-series (sm_120) + Linux only. This REPLACES app/env — there is no
// second venv left behind, because on Blackwell the default cu128 stack is
// strictly worse and keeping it would only waste 13GB.
//
// WHAT YOU END UP WITH
//   torch 2.13.0+cu130      (was 2.7.0+cu128)
//   SageAttention 3         FP4 attention, 2.4-3.3x on long sequences — the
//                           whole point. Selectable as "sage3" in
//                           Settings -> System -> Attention Mode.
//   flash-attn 2.8.3        exact cu130+torch2.13+cp310 build
//   xformers 0.0.35         compiled against this torch (see below)
//   torchao 0.18.0          abi3, genuinely cross-version
//   torchcodec 0.15.0       0.10.0 from requirements.txt is ABI-broken on
//                           torch 2.13 ("undefined symbol: c10::MessageLogger")
//                           and would silently kill video decoding
//
// WHAT YOU LOSE
//   sageattention 2 — no cu130/torch2.13/cp310 wheel exists, so the sage,
//   sage2 and radial modes go. sage3 supersedes them on this hardware.
//
// WHY XFORMERS IS COMPILED RATHER THAN DOWNLOADED
//   PyPI's xformers 0.0.35 is a CPU-only build: it IMPORTS, so Maestro would
//   list "xformers" as an attention mode and then fail mid-generation with
//   "xFormers wasn't build with CUDA support". The cu130 index wheel (0.0.33,
//   cp39-abi3) was built against torch 2.9 / Python 3.12, so its CUDA
//   extensions refuse to load here, and it caps flash-attn at 2.8.2 while we
//   install 2.8.3. v0.0.35 is pinned deliberately: it accepts flash-attn
//   2.7.1-2.8.4, so it tolerates 2.8.3; older tags hard-fail on import.
//
// WHY THE CONDA TOOLCHAIN
//   Both source builds need a host compiler CUDA 13 accepts (<16) AND headers
//   that don't clash with a modern system glibc — on an up-to-date Arch/CachyOS
//   box, nvcc against /usr/include dies with "exception specification is
//   incompatible with that of previous function rsqrt", and the system gcc 16
//   is rejected outright. conda's gxx 14 + sysroot solves both by keeping the
//   build away from /usr/include entirely.
//
// SAFETY
//   The new venv is built alongside the old one and fully verified — imports,
//   numeric checks against sdpa, and Maestro's own attention registry — BEFORE
//   the old one is removed. If any step fails the script stops and app/env is
//   still the working cu128 venv. Reset + Install always returns to default.
module.exports = {
  run: [
    {
      when: "{{gpu !== 'nvidia' || platform !== 'linux'}}",
      method: "notify",
      params: {
        html: "Blackwell Turbo is Linux + NVIDIA only. On Windows there is no cu130 flash-attn wheel for Python 3.10 and no conda toolchain to compile xformers/SageAttention 3, so the swap would be a downgrade."
      },
      next: null
    },
    // Refuse on pre-Blackwell silicon: SageAttention 3's kernels are compiled
    // for sm_100/120/121 only, and shared/attention.py hides sage3 below
    // compute capability 10 — the whole swap would buy nothing.
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "app",
        message: [
          "python -c \"import torch;c=torch.cuda.get_device_capability();assert c[0]>=10, f'SageAttention 3 needs a Blackwell GPU (compute capability >= 10.0); this GPU reports {c[0]}.{c[1]}';print('GPU OK: compute capability',c)\""
        ]
      }
    },
    {
      method: "log",
      params: {
        text: "Building a CUDA 13 venv with SageAttention 3. Expect a ~10GB download plus 20-30 minutes of compiling. Your current venv keeps working until the new one passes verification."
      }
    },
    // ---- Build toolchain -------------------------------------------------
    // CUDA 13.0 toolkit matching torch's cu130, plus conda's own gcc 14 and
    // sysroot. Shared by the xformers and SageAttention 3 builds.
    {
      when: "{{!exists('cuda13-toolkit')}}",
      method: "shell.run",
      params: {
        message: "conda create -y -p cuda13-toolkit -c nvidia -c conda-forge cuda-toolkit=13.0.1 gxx_linux-64=14 sysroot_linux-64=2.28"
      }
    },
    // ---- New venv --------------------------------------------------------
    // --relocatable is what makes the swap at the end safe: console scripts
    // get relative shebangs instead of baking in this build path, so moving
    // the directory to app/env afterwards leaves them working.
    {
      method: "shell.run",
      params: {
        path: "app",
        message: [
          "uv venv --relocatable --python 3.10 env_blackwell"
        ]
      }
    },
    {
      method: "shell.run",
      params: {
        venv: "env_blackwell",
        path: "app",
        message: [
          "uv pip install -r requirements.txt --index-strategy unsafe-best-match",
          "uv pip install hf-xet pip"
        ]
      }
    },
    // torch cu130 + the wheels that have an exact build for it. --force-reinstall
    // because requirements.txt already pulled a default-index torch.
    {
      method: "shell.run",
      params: {
        venv: "env_blackwell",
        path: "app",
        message: [
          "uv pip install torch==2.13.0+cu130 torchvision==0.28.0+cu130 torchaudio==2.11.0+cu130 --index-url https://download.pytorch.org/whl/cu130 --force-reinstall",
          "uv pip install https://github.com/mjun0812/flash-attention-prebuild-wheels/releases/download/v0.9.47/flash_attn-2.8.3%2Bcu130torch2.13-cp310-cp310-linux_x86_64.whl",
          "uv pip install torchao==0.18.0",
          "uv pip install --force-reinstall --no-deps torchcodec==0.15.0",
          "uv pip install ninja"
        ]
      }
    },
    // ---- xformers from source -------------------------------------------
    {
      when: "{{!exists('xformers_src')}}",
      method: "shell.run",
      params: {
        message: "git clone --depth 1 --branch v0.0.35 --recursive https://github.com/facebookresearch/xformers.git xformers_src"
      }
    },
    {
      method: "shell.run",
      params: {
        venv: "../app/env_blackwell",
        path: "xformers_src",
        env: {
          CUDA_HOME: "{{cwd}}/cuda13-toolkit",
          CC: "{{cwd}}/cuda13-toolkit/bin/x86_64-conda-linux-gnu-cc",
          CXX: "{{cwd}}/cuda13-toolkit/bin/x86_64-conda-linux-gnu-c++",
          // nvcc otherwise falls back to the system c++ and fails CUDA 13's
          // host-compiler version check.
          NVCC_PREPEND_FLAGS: "-ccbin {{cwd}}/cuda13-toolkit/bin/x86_64-conda-linux-gnu-c++",
          // Blackwell only. The full arch list turns ~2 minutes into hours.
          TORCH_CUDA_ARCH_LIST: "12.0+PTX",
          MAX_JOBS: "20",
          XFORMERS_BUILD_TYPE: "Release"
        },
        message: [
          "python setup.py bdist_wheel",
          "uv pip install --no-deps dist/xformers-*.whl"
        ]
      }
    },
    // ---- SageAttention 3 from source ------------------------------------
    {
      when: "{{!exists('SageAttention')}}",
      method: "shell.run",
      params: {
        message: "git clone --depth 1 https://github.com/thu-ml/SageAttention"
      }
    },
    // Builds fp4attn_cuda + fp4quant_cuda. setup.py auto-clones CUTLASS (~208MB);
    // the CUTLASS FP4 kernels are the slow part of this script.
    {
      method: "shell.run",
      params: {
        venv: "../../app/env_blackwell",
        path: "SageAttention/sageattention3_blackwell",
        env: {
          CUDA_HOME: "{{cwd}}/cuda13-toolkit",
          CC: "{{cwd}}/cuda13-toolkit/bin/x86_64-conda-linux-gnu-cc",
          CXX: "{{cwd}}/cuda13-toolkit/bin/x86_64-conda-linux-gnu-c++",
          NVCC_PREPEND_FLAGS: "-ccbin {{cwd}}/cuda13-toolkit/bin/x86_64-conda-linux-gnu-c++",
          TORCH_CUDA_ARCH_LIST: "12.0+PTX",
          MAX_JOBS: "8"
        },
        message: [
          "python setup.py bdist_wheel",
          "uv pip install dist/sageattn3-*.whl"
        ]
      }
    },
    // llama.cpp CUDA kernels for GGUF models, mirroring install.js. Soft no-op
    // when no wheel matches this Python/torch/CUDA combo.
    {
      method: "shell.run",
      params: {
        venv: "env_blackwell",
        path: "app",
        message: "python scripts/install_gguf_kernels.py"
      }
    },
    // insightface caches its detection models INSIDE the venv, so carry them
    // over rather than making the user re-download after the swap.
    {
      when: "{{exists('app/env/insightface')}}",
      method: "fs.copy",
      params: {
        src: "app/env/insightface",
        dest: "app/env_blackwell/insightface"
      }
    },
    // ---- Verify BEFORE touching the working venv -------------------------
    {
      method: "shell.run",
      params: {
        venv: "env_blackwell",
        path: "app",
        message: [
          "python -c \"import torch;assert torch.version.cuda.split('.')[0]=='13', f'expected cu13x, got {torch.version.cuda}';print('torch',torch.__version__,'cuda',torch.version.cuda,'cap',torch.cuda.get_device_capability())\"",
          "python -c \"import torchcodec;from torchcodec.decoders import VideoDecoder;print('torchcodec OK',torchcodec.__version__)\"",
          "python -c \"import torchao;print('torchao OK',torchao.__version__)\"",
          "python -c \"import torch,flash_attn;L,H,D=1024,8,128;q,k,v=(torch.randn(L,H,D,dtype=torch.bfloat16,device='cuda') for _ in range(3));cu=torch.tensor([0,L],dtype=torch.int32,device='cuda');x=flash_attn.flash_attn_varlen_func(q=q,k=k,v=v,cu_seqlens_q=cu,cu_seqlens_k=cu,max_seqlen_q=L,max_seqlen_k=L,causal=False);torch.cuda.synchronize();r=torch.nn.functional.scaled_dot_product_attention(q.unsqueeze(0).transpose(1,2),k.unsqueeze(0).transpose(1,2),v.unsqueeze(0).transpose(1,2)).transpose(1,2).reshape(L,H,D);d=(x.float()-r.float()).abs().max().item();assert d<0.05, f'flash-attn diverges from sdpa: {d}';print(f'flash-attn {flash_attn.__version__} OK (max diff {d:.5f})')\"",
          // A CPU-only xformers imports fine and only fails once a generation
          // runs, so check the kernel rather than trusting the import.
          "python -c \"import torch,xformers;from xformers.ops import memory_efficient_attention as mea;q,k,v=(torch.randn(1,1024,8,128,dtype=torch.bfloat16,device='cuda') for _ in range(3));o=mea(q,k,v);torch.cuda.synchronize();r=torch.nn.functional.scaled_dot_product_attention(q.transpose(1,2),k.transpose(1,2),v.transpose(1,2)).transpose(1,2);d=(o.float()-r.float()).abs().max().item();assert d<0.05, f'xformers diverges from sdpa: {d}';print(f'xformers {xformers.__version__} OK (max diff {d:.5f})')\"",
          "python -c \"import torch;from sageattn3 import sageattn3_blackwell;q,k,v=(torch.randn(1,8,2048,128,dtype=torch.bfloat16,device='cuda') for _ in range(3));o=sageattn3_blackwell(q,k,v,is_causal=False);torch.cuda.synchronize();print('SageAttention3 OK:',tuple(o.shape),o.dtype)\"",
          // The decisive check: Maestro itself must offer sage3.
          "python -c \"from shared.attention import get_supported_attention_modes as m;modes=m();assert 'sage3' in modes, f'sage3 missing from {modes}';print('Maestro attention modes:',modes)\""
        ]
      }
    },
    // ---- Swap ------------------------------------------------------------
    // Only reached if every check above passed.
    {
      method: "shell.run",
      params: {
        path: "app",
        message: [
          "rm -rf env",
          "mv env_blackwell env"
        ]
      }
    },
    // update.js skips torch.js while this marker exists, which is exactly what
    // we want — torch.js would reinstall the cu128 stack over the top.
    {
      method: "fs.write",
      params: {
        path: "app/env/.maestro_torch_v1.installed",
        text: "Blackwell venv: torch 2.13.0+cu130. torch.js must NOT run over this — it would reinstall cu128."
      }
    },
    {
      method: "fs.write",
      params: {
        path: "app/env/.maestro_blackwell_v1.installed",
        text: "torch 2.13.0+cu130 + SageAttention 3 + flash-attn 2.8.3 + xformers 0.0.35 + torchao 0.18.0 + torchcodec 0.15.0, built by blackwell.js."
      }
    },
    {
      method: "notify",
      params: {
        html: "Blackwell venv is live. Select SageAttention 3 in Settings -> System -> Attention Mode."
      }
    }
  ]
}
