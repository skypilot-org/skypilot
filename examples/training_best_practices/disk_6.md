[33mTailing logs of job 1 on cluster 'dd'...[0m
[2m├── [0m[2mWaiting for task resources on 2 nodes.[0m
[2m└── [0mJob started. Streaming logs... [2m(Ctrl-C to exit log streaming; job will not be killed)[0m
[36m(setup pid=3476)[0m Channels:
[36m(setup pid=3476)[0m  - nvidia
[36m(setup pid=3476)[0m  - defaults
[36m(setup pid=3476)[0m Platform: linux-64
[36m(setup pid=2560, ip=10.102.30.168)[0m Channels:
[36m(setup pid=2560, ip=10.102.30.168)[0m  - nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m  - defaults
[36m(setup pid=2560, ip=10.102.30.168)[0m Platform: linux-64
[36m(setup pid=3476)[0m Collecting package metadata (repodata.json): ...working... done
[36m(setup pid=3476)[0m Solving environment: ...working... done
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m ## Package Plan ##
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m   environment location: /root/miniconda3
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m   added / updated specs:
[36m(setup pid=3476)[0m     - cuda
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m The following packages will be downloaded:
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m     package                    |            build
[36m(setup pid=3476)[0m     ---------------------------|-----------------
[36m(setup pid=3476)[0m     _sysroot_linux-64_curr_repodata_hack-3|      haa98f57_10          12 KB
[36m(setup pid=3476)[0m     archspec-0.2.3             |     pyhd3eb1b0_0          47 KB
[36m(setup pid=3476)[0m     binutils_impl_linux-64-2.38|       h2a08ee3_1         5.2 MB
[36m(setup pid=3476)[0m     binutils_linux-64-2.38.0   |       hc2dff05_0          24 KB
[36m(setup pid=3476)[0m     ca-certificates-2025.2.25  |       h06a4308_0         129 KB
[36m(setup pid=3476)[0m     certifi-2025.7.14          |  py310h06a4308_0         160 KB
[36m(setup pid=3476)[0m     conda-24.11.3              |  py310h06a4308_0         930 KB
[36m(setup pid=3476)[0m     cpp-expected-1.1.0         |       hdb19cb5_0         130 KB
[36m(setup pid=3476)[0m     cuda-12.9.1                |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-cccl_linux-64-12.9.27 |                0         1.1 MB  nvidia
[36m(setup pid=3476)[0m     cuda-command-line-tools-12.9.1|                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-compiler-12.9.1       |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-crt-dev_linux-64-12.9.86|                0          84 KB  nvidia
[36m(setup pid=3476)[0m     cuda-crt-tools-12.9.86     |                0          20 KB  nvidia
[36m(setup pid=3476)[0m     cuda-cudart-12.9.79        |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-cudart-dev-12.9.79    |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-cudart-dev_linux-64-12.9.79|                0         374 KB  nvidia
[36m(setup pid=3476)[0m     cuda-cudart-static-12.9.79 |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-cudart-static_linux-64-12.9.79|                0         1.1 MB  nvidia
[36m(setup pid=3476)[0m     cuda-cudart_linux-64-12.9.79|                0         189 KB  nvidia
[36m(setup pid=3476)[0m     cuda-cuobjdump-12.9.82     |                1         241 KB  nvidia
[36m(setup pid=3476)[0m     cuda-cupti-12.9.79         |                0         1.8 MB  nvidia
[36m(setup pid=3476)[0m     cuda-cupti-dev-12.9.79     |                0         4.1 MB  nvidia
[36m(setup pid=3476)[0m     cuda-cuxxfilt-12.9.82      |                1         209 KB  nvidia
[36m(setup pid=3476)[0m     cuda-driver-dev-12.9.79    |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-driver-dev_linux-64-12.9.79|                0          31 KB  nvidia
[36m(setup pid=3476)[0m     cuda-gdb-12.9.79           |                1         374 KB  nvidia
[36m(setup pid=3476)[0m     cuda-libraries-12.9.1      |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-libraries-dev-12.9.1  |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nsight-12.9.79        |                0       113.2 MB  nvidia
[36m(setup pid=3476)[0m     cuda-nvcc-12.9.86          |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nvcc-dev_linux-64-12.9.86|                0        13.8 MB  nvidia
[36m(setup pid=3476)[0m     cuda-nvcc-impl-12.9.86     |                0          19 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nvcc-tools-12.9.86    |                0        26.1 MB  nvidia
[36m(setup pid=3476)[0m     cuda-nvcc_linux-64-12.9.86 |                0          20 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nvdisasm-12.9.88      |                1         5.3 MB  nvidia
[36m(setup pid=3476)[0m     cuda-nvml-dev-12.9.79      |                1         136 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nvprof-12.9.79        |                0         2.5 MB  nvidia
[36m(setup pid=3476)[0m     cuda-nvprune-12.9.82       |                1          65 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nvrtc-12.9.86         |                0        64.1 MB  nvidia
[36m(setup pid=3476)[0m     cuda-nvrtc-dev-12.9.86     |                0          31 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nvtx-12.9.79          |                0          24 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nvvm-dev_linux-64-12.9.86|                0          18 KB  nvidia
[36m(setup pid=3476)[0m     cuda-nvvm-impl-12.9.86     |                0        20.5 MB  nvidia
[36m(setup pid=3476)[0m     cuda-nvvm-tools-12.9.86    |                0        23.2 MB  nvidia
[36m(setup pid=3476)[0m     cuda-nvvp-12.9.79          |                1       112.4 MB  nvidia
[36m(setup pid=3476)[0m     cuda-opencl-12.9.19        |                0          25 KB  nvidia
[36m(setup pid=3476)[0m     cuda-opencl-dev-12.9.19    |                0          91 KB  nvidia
[36m(setup pid=3476)[0m     cuda-profiler-api-12.9.79  |                0          19 KB  nvidia
[36m(setup pid=3476)[0m     cuda-runtime-12.9.1        |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-sanitizer-api-12.9.79 |                1         8.8 MB  nvidia
[36m(setup pid=3476)[0m     cuda-toolkit-12.9.1        |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-tools-12.9.1          |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-version-12.9          |                3          17 KB  nvidia
[36m(setup pid=3476)[0m     cuda-visual-tools-12.9.1   |                0          17 KB  nvidia
[36m(setup pid=3476)[0m     dbus-1.13.18               |       hb2f20db_0         504 KB
[36m(setup pid=3476)[0m     expat-2.7.1                |       h6a678d5_0         182 KB
[36m(setup pid=3476)[0m     fontconfig-2.14.1          |       h55d465d_3         281 KB
[36m(setup pid=3476)[0m     freetype-2.13.3            |       h4a9f257_0         686 KB
[36m(setup pid=3476)[0m     frozendict-2.4.2           |  py310h5eee18b_0          55 KB
[36m(setup pid=3476)[0m     gcc_impl_linux-64-11.2.0   |       h1234567_1        22.2 MB
[36m(setup pid=3476)[0m     gcc_linux-64-11.2.0        |       h5c386dc_0          25 KB
[36m(setup pid=3476)[0m     gds-tools-1.14.1.1         |                4        37.7 MB  nvidia
[36m(setup pid=3476)[0m     glib-2.84.2                |       h6a678d5_0         526 KB
[36m(setup pid=3476)[0m     glib-tools-2.84.2          |       h6a678d5_0         119 KB
[36m(setup pid=3476)[0m     gmp-6.3.0                  |       h6a678d5_0         608 KB
[36m(setup pid=3476)[0m     gxx_impl_linux-64-11.2.0   |       h1234567_1        10.6 MB
[36m(setup pid=3476)[0m     gxx_linux-64-11.2.0        |       hc2dff05_0          24 KB
[36m(setup pid=3476)[0m     kernel-headers_linux-64-3.10.0|      h57e8cba_10         952 KB
[36m(setup pid=3476)[0m     libarchive-3.7.7           |       hfab0078_0         936 KB
[36m(setup pid=3476)[0m     libcublas-12.9.1.4         |                0       446.3 MB  nvidia
[36m(setup pid=3476)[0m     libcublas-dev-12.9.1.4     |                0          86 KB  nvidia
[36m(setup pid=3476)[0m     libcufft-11.4.1.4          |                0       154.8 MB  nvidia
[36m(setup pid=3476)[0m     libcufft-dev-11.4.1.4      |                0          29 KB  nvidia
[36m(setup pid=3476)[0m     libcufile-1.14.1.1         |                4         946 KB  nvidia
[36m(setup pid=3476)[0m     libcufile-dev-1.14.1.1     |                4          30 KB  nvidia
[36m(setup pid=3476)[0m     libcurand-10.3.10.19       |                0        44.0 MB  nvidia
[36m(setup pid=3476)[0m     libcurand-dev-10.3.10.19   |                0         238 KB  nvidia
[36m(setup pid=3476)[0m     libcusolver-11.7.5.82      |                0       195.7 MB  nvidia
[36m(setup pid=3476)[0m     libcusolver-dev-11.7.5.82  |                0          57 KB  nvidia
[36m(setup pid=3476)[0m     libcusparse-12.5.10.65     |                0       199.3 MB  nvidia
[36m(setup pid=3476)[0m     libcusparse-dev-12.5.10.65 |                0          46 KB  nvidia
[36m(setup pid=3476)[0m     libgcc-7.2.0               |       h69d50b8_2         269 KB
[36m(setup pid=3476)[0m     libgcc-devel_linux-64-11.2.0|       h1234567_1         2.5 MB
[36m(setup pid=3476)[0m     libglib-2.84.2             |       h37c7471_0         1.7 MB
[36m(setup pid=3476)[0m     libiconv-1.16              |       h5eee18b_3         759 KB
[36m(setup pid=3476)[0m     libmamba-2.0.5             |       haf1ee3a_1         2.2 MB
[36m(setup pid=3476)[0m     libmambapy-2.0.5           |  py310hdb19cb5_1         671 KB
[36m(setup pid=3476)[0m     libnpp-12.4.1.87           |                0       167.6 MB  nvidia
[36m(setup pid=3476)[0m     libnpp-dev-12.4.1.87       |                0         442 KB  nvidia
[36m(setup pid=3476)[0m     libnvfatbin-12.9.82        |                0         799 KB  nvidia
[36m(setup pid=3476)[0m     libnvfatbin-dev-12.9.82    |                0          22 KB  nvidia
[36m(setup pid=3476)[0m     libnvjitlink-12.9.86       |                0        29.2 MB  nvidia
[36m(setup pid=3476)[0m     libnvjitlink-dev-12.9.86   |                0          22 KB  nvidia
[36m(setup pid=3476)[0m     libnvjpeg-12.4.0.76        |                0         3.4 MB  nvidia
[36m(setup pid=3476)[0m     libnvjpeg-dev-12.4.0.76    |                0          27 KB  nvidia
[36m(setup pid=3476)[0m     libpng-1.6.39              |       h5eee18b_0         304 KB
[36m(setup pid=3476)[0m     libsolv-0.7.30             |       he621ea3_1         492 KB
[36m(setup pid=3476)[0m     libstdcxx-devel_linux-64-11.2.0|       h1234567_1        14.6 MB
[36m(setup pid=3476)[0m     libxcb-1.17.0              |       h9b100fa_0         430 KB
[36m(setup pid=3476)[0m     libxkbcommon-1.9.1         |       h69220b7_0         732 KB
[36m(setup pid=3476)[0m     libxml2-2.13.8             |       hfdd30dd_0         739 KB
[36m(setup pid=3476)[0m     nlohmann_json-3.11.2       |       h6a678d5_0         124 KB
[36m(setup pid=3476)[0m     nsight-compute-2025.2.1.3  |                0       319.3 MB  nvidia
[36m(setup pid=3476)[0m     nspr-4.35                  |       h6a678d5_0         244 KB
[36m(setup pid=3476)[0m     nss-3.89.1                 |       h6a678d5_0         2.1 MB
[36m(setup pid=3476)[0m     ocl-icd-2.3.2              |       h5eee18b_1         136 KB
[36m(setup pid=3476)[0m     openssl-3.0.17             |       h5eee18b_0         5.2 MB
[36m(setup pid=3476)[0m     pthread-stubs-0.3          |       h0ce48e5_1           5 KB
[36m(setup pid=3476)[0m     simdjson-3.10.1            |       hdb19cb5_0         258 KB
[36m(setup pid=3476)[0m     spdlog-1.11.0              |       hdb19cb5_0         234 KB
[36m(setup pid=3476)[0m     sysroot_linux-64-2.17      |      h57e8cba_10        32.6 MB
[36m(setup pid=3476)[0m     xkeyboard-config-2.44      |       h5eee18b_0         411 KB
[36m(setup pid=3476)[0m     xorg-libx11-1.8.12         |       h9b100fa_1         895 KB
[36m(setup pid=3476)[0m     xorg-libxau-1.0.12         |       h9b100fa_0          13 KB
[36m(setup pid=3476)[0m     xorg-libxdmcp-1.1.5        |       h9b100fa_0          19 KB
[36m(setup pid=3476)[0m     xorg-xorgproto-2024.1      |       h5eee18b_1         580 KB
[36m(setup pid=3476)[0m     xz-5.6.4                   |       h5eee18b_1         567 KB
[36m(setup pid=3476)[0m     ------------------------------------------------------------
[36m(setup pid=3476)[0m                                            Total:        2.06 GB
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m The following NEW packages will be INSTALLED:
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m   _sysroot_linux-64~ pkgs/main/noarch::_sysroot_linux-64_curr_repodata_hack-3-haa98f57_10 
[36m(setup pid=3476)[0m   binutils_impl_lin~ pkgs/main/linux-64::binutils_impl_linux-64-2.38-h2a08ee3_1 
[36m(setup pid=3476)[0m   binutils_linux-64  pkgs/main/linux-64::binutils_linux-64-2.38.0-hc2dff05_0 
[36m(setup pid=3476)[0m   cpp-expected       pkgs/main/linux-64::cpp-expected-1.1.0-hdb19cb5_0 
[36m(setup pid=3476)[0m   cuda               nvidia/linux-64::cuda-12.9.1-0 
[36m(setup pid=3476)[0m   cuda-cccl_linux-64 nvidia/linux-64::cuda-cccl_linux-64-12.9.27-0 
[36m(setup pid=3476)[0m   cuda-command-line~ nvidia/linux-64::cuda-command-line-tools-12.9.1-0 
[36m(setup pid=3476)[0m   cuda-compiler      nvidia/linux-64::cuda-compiler-12.9.1-0 
[36m(setup pid=3476)[0m   cuda-crt-dev_linu~ nvidia/noarch::cuda-crt-dev_linux-64-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-crt-tools     nvidia/linux-64::cuda-crt-tools-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-cudart        nvidia/linux-64::cuda-cudart-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-cudart-dev    nvidia/linux-64::cuda-cudart-dev-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-cudart-dev_l~ nvidia/noarch::cuda-cudart-dev_linux-64-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-cudart-static nvidia/linux-64::cuda-cudart-static-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-cudart-stati~ nvidia/noarch::cuda-cudart-static_linux-64-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-cudart_linux~ nvidia/noarch::cuda-cudart_linux-64-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-cuobjdump     nvidia/linux-64::cuda-cuobjdump-12.9.82-1 
[36m(setup pid=3476)[0m   cuda-cupti         nvidia/linux-64::cuda-cupti-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-cupti-dev     nvidia/linux-64::cuda-cupti-dev-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-cuxxfilt      nvidia/linux-64::cuda-cuxxfilt-12.9.82-1 
[36m(setup pid=3476)[0m   cuda-driver-dev    nvidia/linux-64::cuda-driver-dev-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-driver-dev_l~ nvidia/noarch::cuda-driver-dev_linux-64-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-gdb           nvidia/linux-64::cuda-gdb-12.9.79-1 
[36m(setup pid=3476)[0m   cuda-libraries     nvidia/linux-64::cuda-libraries-12.9.1-0 
[36m(setup pid=3476)[0m   cuda-libraries-dev nvidia/linux-64::cuda-libraries-dev-12.9.1-0 
[36m(setup pid=3476)[0m   cuda-nsight        nvidia/linux-64::cuda-nsight-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-nvcc          nvidia/linux-64::cuda-nvcc-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvcc-dev_lin~ nvidia/noarch::cuda-nvcc-dev_linux-64-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvcc-impl     nvidia/linux-64::cuda-nvcc-impl-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvcc-tools    nvidia/linux-64::cuda-nvcc-tools-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvcc_linux-64 nvidia/linux-64::cuda-nvcc_linux-64-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvdisasm      nvidia/linux-64::cuda-nvdisasm-12.9.88-1 
[36m(setup pid=3476)[0m   cuda-nvml-dev      nvidia/linux-64::cuda-nvml-dev-12.9.79-1 
[36m(setup pid=3476)[0m   cuda-nvprof        nvidia/linux-64::cuda-nvprof-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-nvprune       nvidia/linux-64::cuda-nvprune-12.9.82-1 
[36m(setup pid=3476)[0m   cuda-nvrtc         nvidia/linux-64::cuda-nvrtc-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvrtc-dev     nvidia/linux-64::cuda-nvrtc-dev-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvtx          nvidia/linux-64::cuda-nvtx-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-nvvm-dev_lin~ nvidia/noarch::cuda-nvvm-dev_linux-64-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvvm-impl     nvidia/linux-64::cuda-nvvm-impl-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvvm-tools    nvidia/linux-64::cuda-nvvm-tools-12.9.86-0 
[36m(setup pid=3476)[0m   cuda-nvvp          nvidia/linux-64::cuda-nvvp-12.9.79-1 
[36m(setup pid=3476)[0m   cuda-opencl        nvidia/linux-64::cuda-opencl-12.9.19-0 
[36m(setup pid=3476)[0m   cuda-opencl-dev    nvidia/linux-64::cuda-opencl-dev-12.9.19-0 
[36m(setup pid=3476)[0m   cuda-profiler-api  nvidia/linux-64::cuda-profiler-api-12.9.79-0 
[36m(setup pid=3476)[0m   cuda-runtime       nvidia/linux-64::cuda-runtime-12.9.1-0 
[36m(setup pid=3476)[0m   cuda-sanitizer-api nvidia/linux-64::cuda-sanitizer-api-12.9.79-1 
[36m(setup pid=3476)[0m   cuda-toolkit       nvidia/linux-64::cuda-toolkit-12.9.1-0 
[36m(setup pid=3476)[0m   cuda-tools         nvidia/linux-64::cuda-tools-12.9.1-0 
[36m(setup pid=3476)[0m   cuda-version       nvidia/noarch::cuda-version-12.9-3 
[36m(setup pid=3476)[0m   cuda-visual-tools  nvidia/linux-64::cuda-visual-tools-12.9.1-0 
[36m(setup pid=3476)[0m   dbus               pkgs/main/linux-64::dbus-1.13.18-hb2f20db_0 
[36m(setup pid=3476)[0m   expat              pkgs/main/linux-64::expat-2.7.1-h6a678d5_0 
[36m(setup pid=3476)[0m   fontconfig         pkgs/main/linux-64::fontconfig-2.14.1-h55d465d_3 
[36m(setup pid=3476)[0m   freetype           pkgs/main/linux-64::freetype-2.13.3-h4a9f257_0 
[36m(setup pid=3476)[0m   frozendict         pkgs/main/linux-64::frozendict-2.4.2-py310h5eee18b_0 
[36m(setup pid=3476)[0m   gcc_impl_linux-64  pkgs/main/linux-64::gcc_impl_linux-64-11.2.0-h1234567_1 
[36m(setup pid=3476)[0m   gcc_linux-64       pkgs/main/linux-64::gcc_linux-64-11.2.0-h5c386dc_0 
[36m(setup pid=3476)[0m   gds-tools          nvidia/linux-64::gds-tools-1.14.1.1-4 
[36m(setup pid=3476)[0m   glib               pkgs/main/linux-64::glib-2.84.2-h6a678d5_0 
[36m(setup pid=3476)[0m   glib-tools         pkgs/main/linux-64::glib-tools-2.84.2-h6a678d5_0 
[36m(setup pid=3476)[0m   gmp                pkgs/main/linux-64::gmp-6.3.0-h6a678d5_0 
[36m(setup pid=3476)[0m   gxx_impl_linux-64  pkgs/main/linux-64::gxx_impl_linux-64-11.2.0-h1234567_1 
[36m(setup pid=3476)[0m   gxx_linux-64       pkgs/main/linux-64::gxx_linux-64-11.2.0-hc2dff05_0 
[36m(setup pid=3476)[0m   kernel-headers_li~ pkgs/main/noarch::kernel-headers_linux-64-3.10.0-h57e8cba_10 
[36m(setup pid=3476)[0m   libcublas          nvidia/linux-64::libcublas-12.9.1.4-0 
[36m(setup pid=3476)[0m   libcublas-dev      nvidia/linux-64::libcublas-dev-12.9.1.4-0 
[36m(setup pid=3476)[0m   libcufft           nvidia/linux-64::libcufft-11.4.1.4-0 
[36m(setup pid=3476)[0m   libcufft-dev       nvidia/linux-64::libcufft-dev-11.4.1.4-0 
[36m(setup pid=3476)[0m   libcufile          nvidia/linux-64::libcufile-1.14.1.1-4 
[36m(setup pid=3476)[0m   libcufile-dev      nvidia/linux-64::libcufile-dev-1.14.1.1-4 
[36m(setup pid=3476)[0m   libcurand          nvidia/linux-64::libcurand-10.3.10.19-0 
[36m(setup pid=3476)[0m   libcurand-dev      nvidia/linux-64::libcurand-dev-10.3.10.19-0 
[36m(setup pid=3476)[0m   libcusolver        nvidia/linux-64::libcusolver-11.7.5.82-0 
[36m(setup pid=3476)[0m   libcusolver-dev    nvidia/linux-64::libcusolver-dev-11.7.5.82-0 
[36m(setup pid=3476)[0m   libcusparse        nvidia/linux-64::libcusparse-12.5.10.65-0 
[36m(setup pid=3476)[0m   libcusparse-dev    nvidia/linux-64::libcusparse-dev-12.5.10.65-0 
[36m(setup pid=3476)[0m   libgcc             pkgs/main/linux-64::libgcc-7.2.0-h69d50b8_2 
[36m(setup pid=3476)[0m   libgcc-devel_linu~ pkgs/main/linux-64::libgcc-devel_linux-64-11.2.0-h1234567_1 
[36m(setup pid=3476)[0m   libglib            pkgs/main/linux-64::libglib-2.84.2-h37c7471_0 
[36m(setup pid=3476)[0m   libiconv           pkgs/main/linux-64::libiconv-1.16-h5eee18b_3 
[36m(setup pid=3476)[0m   libnpp             nvidia/linux-64::libnpp-12.4.1.87-0 
[36m(setup pid=3476)[0m   libnpp-dev         nvidia/linux-64::libnpp-dev-12.4.1.87-0 
[36m(setup pid=3476)[0m   libnvfatbin        nvidia/linux-64::libnvfatbin-12.9.82-0 
[36m(setup pid=3476)[0m   libnvfatbin-dev    nvidia/linux-64::libnvfatbin-dev-12.9.82-0 
[36m(setup pid=3476)[0m   libnvjitlink       nvidia/linux-64::libnvjitlink-12.9.86-0 
[36m(setup pid=3476)[0m   libnvjitlink-dev   nvidia/linux-64::libnvjitlink-dev-12.9.86-0 
[36m(setup pid=3476)[0m   libnvjpeg          nvidia/linux-64::libnvjpeg-12.4.0.76-0 
[36m(setup pid=3476)[0m   libnvjpeg-dev      nvidia/linux-64::libnvjpeg-dev-12.4.0.76-0 
[36m(setup pid=3476)[0m   libpng             pkgs/main/linux-64::libpng-1.6.39-h5eee18b_0 
[36m(setup pid=3476)[0m   libstdcxx-devel_l~ pkgs/main/linux-64::libstdcxx-devel_linux-64-11.2.0-h1234567_1 
[36m(setup pid=3476)[0m   libxcb             pkgs/main/linux-64::libxcb-1.17.0-h9b100fa_0 
[36m(setup pid=3476)[0m   libxkbcommon       pkgs/main/linux-64::libxkbcommon-1.9.1-h69220b7_0 
[36m(setup pid=3476)[0m   nlohmann_json      pkgs/main/linux-64::nlohmann_json-3.11.2-h6a678d5_0 
[36m(setup pid=3476)[0m   nsight-compute     nvidia/linux-64::nsight-compute-2025.2.1.3-0 
[36m(setup pid=3476)[0m   nspr               pkgs/main/linux-64::nspr-4.35-h6a678d5_0 
[36m(setup pid=3476)[0m   nss                pkgs/main/linux-64::nss-3.89.1-h6a678d5_0 
[36m(setup pid=3476)[0m   ocl-icd            pkgs/main/linux-64::ocl-icd-2.3.2-h5eee18b_1 
[36m(setup pid=3476)[0m   pthread-stubs      pkgs/main/linux-64::pthread-stubs-0.3-h0ce48e5_1 
[36m(setup pid=3476)[0m   simdjson           pkgs/main/linux-64::simdjson-3.10.1-hdb19cb5_0 
[36m(setup pid=3476)[0m   spdlog             pkgs/main/linux-64::spdlog-1.11.0-hdb19cb5_0 
[36m(setup pid=3476)[0m   sysroot_linux-64   pkgs/main/noarch::sysroot_linux-64-2.17-h57e8cba_10 
[36m(setup pid=3476)[0m   xkeyboard-config   pkgs/main/linux-64::xkeyboard-config-2.44-h5eee18b_0 
[36m(setup pid=3476)[0m   xorg-libx11        pkgs/main/linux-64::xorg-libx11-1.8.12-h9b100fa_1 
[36m(setup pid=3476)[0m   xorg-libxau        pkgs/main/linux-64::xorg-libxau-1.0.12-h9b100fa_0 
[36m(setup pid=3476)[0m   xorg-libxdmcp      pkgs/main/linux-64::xorg-libxdmcp-1.1.5-h9b100fa_0 
[36m(setup pid=3476)[0m   xorg-xorgproto     pkgs/main/linux-64::xorg-xorgproto-2024.1-h5eee18b_1 
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m The following packages will be UPDATED:
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m   archspec                               0.2.1-pyhd3eb1b0_0 --> 0.2.3-pyhd3eb1b0_0 
[36m(setup pid=3476)[0m   ca-certificates                     2023.12.12-h06a4308_0 --> 2025.2.25-h06a4308_0 
[36m(setup pid=3476)[0m   certifi                        2023.11.17-py310h06a4308_0 --> 2025.7.14-py310h06a4308_0 
[36m(setup pid=3476)[0m   conda                             23.11.0-py310h06a4308_0 --> 24.11.3-py310h06a4308_0 
[36m(setup pid=3476)[0m   libarchive                               3.6.2-h6ac8c49_2 --> 3.7.7-hfab0078_0 
[36m(setup pid=3476)[0m   libmamba                                 1.5.3-haf1ee3a_0 --> 2.0.5-haf1ee3a_1 
[36m(setup pid=3476)[0m   libmambapy                          1.5.3-py310h2dafd23_0 --> 2.0.5-py310hdb19cb5_1 
[36m(setup pid=3476)[0m   libsolv                                 0.7.24-he621ea3_0 --> 0.7.30-he621ea3_1 
[36m(setup pid=3476)[0m   libxml2                                 2.10.4-hf1b16e4_1 --> 2.13.8-hfdd30dd_0 
[36m(setup pid=3476)[0m   openssl                                 3.0.12-h7f8727e_0 --> 3.0.17-h5eee18b_0 
[36m(setup pid=3476)[0m   xz                                       5.4.5-h5eee18b_0 --> 5.6.4-h5eee18b_1 
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m Proceed ([y]/n)? 
[36m(setup pid=3476)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m Collecting package metadata (repodata.json): ...working... done
[36m(setup pid=2560, ip=10.102.30.168)[0m Solving environment: ...working... done
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m ## Package Plan ##
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m   environment location: /root/miniconda3
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m   added / updated specs:
[36m(setup pid=2560, ip=10.102.30.168)[0m     - cuda
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m The following packages will be downloaded:
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m     package                    |            build
[36m(setup pid=2560, ip=10.102.30.168)[0m     ---------------------------|-----------------
[36m(setup pid=2560, ip=10.102.30.168)[0m     _sysroot_linux-64_curr_repodata_hack-3|      haa98f57_10          12 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     archspec-0.2.3             |     pyhd3eb1b0_0          47 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     binutils_impl_linux-64-2.38|       h2a08ee3_1         5.2 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     binutils_linux-64-2.38.0   |       hc2dff05_0          24 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     ca-certificates-2025.2.25  |       h06a4308_0         129 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     certifi-2025.7.14          |  py310h06a4308_0         160 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     conda-24.11.3              |  py310h06a4308_0         930 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     cpp-expected-1.1.0         |       hdb19cb5_0         130 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-12.9.1                |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cccl_linux-64-12.9.27 |                0         1.1 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-command-line-tools-12.9.1|                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-compiler-12.9.1       |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-crt-dev_linux-64-12.9.86|                0          84 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-crt-tools-12.9.86     |                0          20 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cudart-12.9.79        |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cudart-dev-12.9.79    |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cudart-dev_linux-64-12.9.79|                0         374 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cudart-static-12.9.79 |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cudart-static_linux-64-12.9.79|                0         1.1 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cudart_linux-64-12.9.79|                0         189 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cuobjdump-12.9.82     |                1         241 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cupti-12.9.79         |                0         1.8 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cupti-dev-12.9.79     |                0         4.1 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-cuxxfilt-12.9.82      |                1         209 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-driver-dev-12.9.79    |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-driver-dev_linux-64-12.9.79|                0          31 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-gdb-12.9.79           |                1         374 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-libraries-12.9.1      |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-libraries-dev-12.9.1  |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nsight-12.9.79        |                0       113.2 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvcc-12.9.86          |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvcc-dev_linux-64-12.9.86|                0        13.8 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvcc-impl-12.9.86     |                0          19 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvcc-tools-12.9.86    |                0        26.1 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvcc_linux-64-12.9.86 |                0          20 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvdisasm-12.9.88      |                1         5.3 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvml-dev-12.9.79      |                1         136 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvprof-12.9.79        |                0         2.5 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvprune-12.9.82       |                1          65 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvrtc-12.9.86         |                0        64.1 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvrtc-dev-12.9.86     |                0          31 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvtx-12.9.79          |                0          24 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvvm-dev_linux-64-12.9.86|                0          18 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvvm-impl-12.9.86     |                0        20.5 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvvm-tools-12.9.86    |                0        23.2 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-nvvp-12.9.79          |                1       112.4 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-opencl-12.9.19        |                0          25 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-opencl-dev-12.9.19    |                0          91 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-profiler-api-12.9.79  |                0          19 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-runtime-12.9.1        |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-sanitizer-api-12.9.79 |                1         8.8 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-toolkit-12.9.1        |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-tools-12.9.1          |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-version-12.9          |                3          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     cuda-visual-tools-12.9.1   |                0          17 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     dbus-1.13.18               |       hb2f20db_0         504 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     expat-2.7.1                |       h6a678d5_0         182 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     fontconfig-2.14.1          |       h55d465d_3         281 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     freetype-2.13.3            |       h4a9f257_0         686 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     frozendict-2.4.2           |  py310h5eee18b_0          55 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     gcc_impl_linux-64-11.2.0   |       h1234567_1        22.2 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     gcc_linux-64-11.2.0        |       h5c386dc_0          25 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     gds-tools-1.14.1.1         |                4        37.7 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     glib-2.84.2                |       h6a678d5_0         526 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     glib-tools-2.84.2          |       h6a678d5_0         119 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     gmp-6.3.0                  |       h6a678d5_0         608 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     gxx_impl_linux-64-11.2.0   |       h1234567_1        10.6 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     gxx_linux-64-11.2.0        |       hc2dff05_0          24 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     kernel-headers_linux-64-3.10.0|      h57e8cba_10         952 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libarchive-3.7.7           |       hfab0078_0         936 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcublas-12.9.1.4         |                0       446.3 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcublas-dev-12.9.1.4     |                0          86 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcufft-11.4.1.4          |                0       154.8 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcufft-dev-11.4.1.4      |                0          29 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcufile-1.14.1.1         |                4         946 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcufile-dev-1.14.1.1     |                4          30 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcurand-10.3.10.19       |                0        44.0 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcurand-dev-10.3.10.19   |                0         238 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcusolver-11.7.5.82      |                0       195.7 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcusolver-dev-11.7.5.82  |                0          57 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcusparse-12.5.10.65     |                0       199.3 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libcusparse-dev-12.5.10.65 |                0          46 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libgcc-7.2.0               |       h69d50b8_2         269 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libgcc-devel_linux-64-11.2.0|       h1234567_1         2.5 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libglib-2.84.2             |       h37c7471_0         1.7 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libiconv-1.16              |       h5eee18b_3         759 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libmamba-2.0.5             |       haf1ee3a_1         2.2 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libmambapy-2.0.5           |  py310hdb19cb5_1         671 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libnpp-12.4.1.87           |                0       167.6 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libnpp-dev-12.4.1.87       |                0         442 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libnvfatbin-12.9.82        |                0         799 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libnvfatbin-dev-12.9.82    |                0          22 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libnvjitlink-12.9.86       |                0        29.2 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libnvjitlink-dev-12.9.86   |                0          22 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libnvjpeg-12.4.0.76        |                0         3.4 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libnvjpeg-dev-12.4.0.76    |                0          27 KB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     libpng-1.6.39              |       h5eee18b_0         304 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libsolv-0.7.30             |       he621ea3_1         492 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libstdcxx-devel_linux-64-11.2.0|       h1234567_1        14.6 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libxcb-1.17.0              |       h9b100fa_0         430 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libxkbcommon-1.9.1         |       h69220b7_0         732 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     libxml2-2.13.8             |       hfdd30dd_0         739 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     nlohmann_json-3.11.2       |       h6a678d5_0         124 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     nsight-compute-2025.2.1.3  |                0       319.3 MB  nvidia
[36m(setup pid=2560, ip=10.102.30.168)[0m     nspr-4.35                  |       h6a678d5_0         244 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     nss-3.89.1                 |       h6a678d5_0         2.1 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     ocl-icd-2.3.2              |       h5eee18b_1         136 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     openssl-3.0.17             |       h5eee18b_0         5.2 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     pthread-stubs-0.3          |       h0ce48e5_1           5 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     simdjson-3.10.1            |       hdb19cb5_0         258 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     spdlog-1.11.0              |       hdb19cb5_0         234 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     sysroot_linux-64-2.17      |      h57e8cba_10        32.6 MB
[36m(setup pid=2560, ip=10.102.30.168)[0m     xkeyboard-config-2.44      |       h5eee18b_0         411 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     xorg-libx11-1.8.12         |       h9b100fa_1         895 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     xorg-libxau-1.0.12         |       h9b100fa_0          13 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     xorg-libxdmcp-1.1.5        |       h9b100fa_0          19 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     xorg-xorgproto-2024.1      |       h5eee18b_1         580 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     xz-5.6.4                   |       h5eee18b_1         567 KB
[36m(setup pid=2560, ip=10.102.30.168)[0m     ------------------------------------------------------------
[36m(setup pid=2560, ip=10.102.30.168)[0m                                            Total:        2.06 GB
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m The following NEW packages will be INSTALLED:
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m   _sysroot_linux-64~ pkgs/main/noarch::_sysroot_linux-64_curr_repodata_hack-3-haa98f57_10 
[36m(setup pid=2560, ip=10.102.30.168)[0m   binutils_impl_lin~ pkgs/main/linux-64::binutils_impl_linux-64-2.38-h2a08ee3_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   binutils_linux-64  pkgs/main/linux-64::binutils_linux-64-2.38.0-hc2dff05_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cpp-expected       pkgs/main/linux-64::cpp-expected-1.1.0-hdb19cb5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda               nvidia/linux-64::cuda-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cccl_linux-64 nvidia/linux-64::cuda-cccl_linux-64-12.9.27-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-command-line~ nvidia/linux-64::cuda-command-line-tools-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-compiler      nvidia/linux-64::cuda-compiler-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-crt-dev_linu~ nvidia/noarch::cuda-crt-dev_linux-64-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-crt-tools     nvidia/linux-64::cuda-crt-tools-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cudart        nvidia/linux-64::cuda-cudart-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cudart-dev    nvidia/linux-64::cuda-cudart-dev-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cudart-dev_l~ nvidia/noarch::cuda-cudart-dev_linux-64-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cudart-static nvidia/linux-64::cuda-cudart-static-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cudart-stati~ nvidia/noarch::cuda-cudart-static_linux-64-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cudart_linux~ nvidia/noarch::cuda-cudart_linux-64-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cuobjdump     nvidia/linux-64::cuda-cuobjdump-12.9.82-1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cupti         nvidia/linux-64::cuda-cupti-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cupti-dev     nvidia/linux-64::cuda-cupti-dev-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-cuxxfilt      nvidia/linux-64::cuda-cuxxfilt-12.9.82-1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-driver-dev    nvidia/linux-64::cuda-driver-dev-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-driver-dev_l~ nvidia/noarch::cuda-driver-dev_linux-64-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-gdb           nvidia/linux-64::cuda-gdb-12.9.79-1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-libraries     nvidia/linux-64::cuda-libraries-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-libraries-dev nvidia/linux-64::cuda-libraries-dev-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nsight        nvidia/linux-64::cuda-nsight-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvcc          nvidia/linux-64::cuda-nvcc-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvcc-dev_lin~ nvidia/noarch::cuda-nvcc-dev_linux-64-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvcc-impl     nvidia/linux-64::cuda-nvcc-impl-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvcc-tools    nvidia/linux-64::cuda-nvcc-tools-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvcc_linux-64 nvidia/linux-64::cuda-nvcc_linux-64-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvdisasm      nvidia/linux-64::cuda-nvdisasm-12.9.88-1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvml-dev      nvidia/linux-64::cuda-nvml-dev-12.9.79-1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvprof        nvidia/linux-64::cuda-nvprof-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvprune       nvidia/linux-64::cuda-nvprune-12.9.82-1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvrtc         nvidia/linux-64::cuda-nvrtc-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvrtc-dev     nvidia/linux-64::cuda-nvrtc-dev-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvtx          nvidia/linux-64::cuda-nvtx-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvvm-dev_lin~ nvidia/noarch::cuda-nvvm-dev_linux-64-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvvm-impl     nvidia/linux-64::cuda-nvvm-impl-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvvm-tools    nvidia/linux-64::cuda-nvvm-tools-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-nvvp          nvidia/linux-64::cuda-nvvp-12.9.79-1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-opencl        nvidia/linux-64::cuda-opencl-12.9.19-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-opencl-dev    nvidia/linux-64::cuda-opencl-dev-12.9.19-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-profiler-api  nvidia/linux-64::cuda-profiler-api-12.9.79-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-runtime       nvidia/linux-64::cuda-runtime-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-sanitizer-api nvidia/linux-64::cuda-sanitizer-api-12.9.79-1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-toolkit       nvidia/linux-64::cuda-toolkit-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-tools         nvidia/linux-64::cuda-tools-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-version       nvidia/noarch::cuda-version-12.9-3 
[36m(setup pid=2560, ip=10.102.30.168)[0m   cuda-visual-tools  nvidia/linux-64::cuda-visual-tools-12.9.1-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   dbus               pkgs/main/linux-64::dbus-1.13.18-hb2f20db_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   expat              pkgs/main/linux-64::expat-2.7.1-h6a678d5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   fontconfig         pkgs/main/linux-64::fontconfig-2.14.1-h55d465d_3 
[36m(setup pid=2560, ip=10.102.30.168)[0m   freetype           pkgs/main/linux-64::freetype-2.13.3-h4a9f257_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   frozendict         pkgs/main/linux-64::frozendict-2.4.2-py310h5eee18b_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   gcc_impl_linux-64  pkgs/main/linux-64::gcc_impl_linux-64-11.2.0-h1234567_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   gcc_linux-64       pkgs/main/linux-64::gcc_linux-64-11.2.0-h5c386dc_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   gds-tools          nvidia/linux-64::gds-tools-1.14.1.1-4 
[36m(setup pid=2560, ip=10.102.30.168)[0m   glib               pkgs/main/linux-64::glib-2.84.2-h6a678d5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   glib-tools         pkgs/main/linux-64::glib-tools-2.84.2-h6a678d5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   gmp                pkgs/main/linux-64::gmp-6.3.0-h6a678d5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   gxx_impl_linux-64  pkgs/main/linux-64::gxx_impl_linux-64-11.2.0-h1234567_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   gxx_linux-64       pkgs/main/linux-64::gxx_linux-64-11.2.0-hc2dff05_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   kernel-headers_li~ pkgs/main/noarch::kernel-headers_linux-64-3.10.0-h57e8cba_10 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcublas          nvidia/linux-64::libcublas-12.9.1.4-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcublas-dev      nvidia/linux-64::libcublas-dev-12.9.1.4-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcufft           nvidia/linux-64::libcufft-11.4.1.4-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcufft-dev       nvidia/linux-64::libcufft-dev-11.4.1.4-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcufile          nvidia/linux-64::libcufile-1.14.1.1-4 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcufile-dev      nvidia/linux-64::libcufile-dev-1.14.1.1-4 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcurand          nvidia/linux-64::libcurand-10.3.10.19-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcurand-dev      nvidia/linux-64::libcurand-dev-10.3.10.19-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcusolver        nvidia/linux-64::libcusolver-11.7.5.82-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcusolver-dev    nvidia/linux-64::libcusolver-dev-11.7.5.82-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcusparse        nvidia/linux-64::libcusparse-12.5.10.65-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcusparse-dev    nvidia/linux-64::libcusparse-dev-12.5.10.65-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libgcc             pkgs/main/linux-64::libgcc-7.2.0-h69d50b8_2 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libgcc-devel_linu~ pkgs/main/linux-64::libgcc-devel_linux-64-11.2.0-h1234567_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libglib            pkgs/main/linux-64::libglib-2.84.2-h37c7471_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libiconv           pkgs/main/linux-64::libiconv-1.16-h5eee18b_3 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libnpp             nvidia/linux-64::libnpp-12.4.1.87-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libnpp-dev         nvidia/linux-64::libnpp-dev-12.4.1.87-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libnvfatbin        nvidia/linux-64::libnvfatbin-12.9.82-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libnvfatbin-dev    nvidia/linux-64::libnvfatbin-dev-12.9.82-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libnvjitlink       nvidia/linux-64::libnvjitlink-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libnvjitlink-dev   nvidia/linux-64::libnvjitlink-dev-12.9.86-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libnvjpeg          nvidia/linux-64::libnvjpeg-12.4.0.76-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libnvjpeg-dev      nvidia/linux-64::libnvjpeg-dev-12.4.0.76-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libpng             pkgs/main/linux-64::libpng-1.6.39-h5eee18b_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libstdcxx-devel_l~ pkgs/main/linux-64::libstdcxx-devel_linux-64-11.2.0-h1234567_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libxcb             pkgs/main/linux-64::libxcb-1.17.0-h9b100fa_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libxkbcommon       pkgs/main/linux-64::libxkbcommon-1.9.1-h69220b7_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   nlohmann_json      pkgs/main/linux-64::nlohmann_json-3.11.2-h6a678d5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   nsight-compute     nvidia/linux-64::nsight-compute-2025.2.1.3-0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   nspr               pkgs/main/linux-64::nspr-4.35-h6a678d5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   nss                pkgs/main/linux-64::nss-3.89.1-h6a678d5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   ocl-icd            pkgs/main/linux-64::ocl-icd-2.3.2-h5eee18b_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   pthread-stubs      pkgs/main/linux-64::pthread-stubs-0.3-h0ce48e5_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   simdjson           pkgs/main/linux-64::simdjson-3.10.1-hdb19cb5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   spdlog             pkgs/main/linux-64::spdlog-1.11.0-hdb19cb5_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   sysroot_linux-64   pkgs/main/noarch::sysroot_linux-64-2.17-h57e8cba_10 
[36m(setup pid=2560, ip=10.102.30.168)[0m   xkeyboard-config   pkgs/main/linux-64::xkeyboard-config-2.44-h5eee18b_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   xorg-libx11        pkgs/main/linux-64::xorg-libx11-1.8.12-h9b100fa_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   xorg-libxau        pkgs/main/linux-64::xorg-libxau-1.0.12-h9b100fa_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   xorg-libxdmcp      pkgs/main/linux-64::xorg-libxdmcp-1.1.5-h9b100fa_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   xorg-xorgproto     pkgs/main/linux-64::xorg-xorgproto-2024.1-h5eee18b_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m The following packages will be UPDATED:
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m   archspec                               0.2.1-pyhd3eb1b0_0 --> 0.2.3-pyhd3eb1b0_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   ca-certificates                     2023.12.12-h06a4308_0 --> 2025.2.25-h06a4308_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   certifi                        2023.11.17-py310h06a4308_0 --> 2025.7.14-py310h06a4308_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   conda                             23.11.0-py310h06a4308_0 --> 24.11.3-py310h06a4308_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libarchive                               3.6.2-h6ac8c49_2 --> 3.7.7-hfab0078_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libmamba                                 1.5.3-haf1ee3a_0 --> 2.0.5-haf1ee3a_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libmambapy                          1.5.3-py310h2dafd23_0 --> 2.0.5-py310hdb19cb5_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libsolv                                 0.7.24-he621ea3_0 --> 0.7.30-he621ea3_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m   libxml2                                 2.10.4-hf1b16e4_1 --> 2.13.8-hfdd30dd_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   openssl                                 3.0.12-h7f8727e_0 --> 3.0.17-h5eee18b_0 
[36m(setup pid=2560, ip=10.102.30.168)[0m   xz                                       5.4.5-h5eee18b_0 --> 5.6.4-h5eee18b_1 
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m Proceed ([y]/n)? 
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=3476)[0m Downloading and Extracting Packages: ...working... done
[36m(setup pid=3476)[0m Preparing transaction: ...working... done
[36m(setup pid=3476)[0m Verifying transaction: ...working... done
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading and Extracting Packages: ...working... done
[36m(setup pid=3476)[0m Executing transaction: ...working... done
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing transaction: ...working... done
[36m(setup pid=3476)[0m Error while loading conda entry point: conda-libmamba-solver (module 'libmambapy' has no attribute 'QueryFormat')
[36m(setup pid=3476)[0m Using CPython 3.10.12 interpreter at: /usr/bin/python3.10
[36m(setup pid=3476)[0m Creating virtual environment with seed packages at: /root/training
[36m(setup pid=3476)[0m  + pip==25.2
[36m(setup pid=3476)[0m  + setuptools==80.9.0
[36m(setup pid=3476)[0m  + wheel==0.45.1
[36m(setup pid=3476)[0m Using Python 3.10.12 environment at: /root/training
[36m(setup pid=3476)[0m Resolved 29 packages in 127ms
[36m(setup pid=3476)[0m Downloading triton (148.4MiB)
[36m(setup pid=3476)[0m Downloading nvidia-nvjitlink-cu12 (18.8MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cufile-cu12 (1.1MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cufft-cu12 (190.9MiB)
[36m(setup pid=3476)[0m Downloading torchaudio (3.3MiB)
[36m(setup pid=3476)[0m Downloading nvidia-curand-cu12 (53.7MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cudnn-cu12 (544.5MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cuda-nvrtc-cu12 (22.6MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cusolver-cu12 (150.9MiB)
[36m(setup pid=3476)[0m Downloading torchvision (7.1MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cusparselt-cu12 (149.5MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cublas-cu12 (374.9MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cuda-cupti-cu12 (8.5MiB)
[36m(setup pid=3476)[0m Downloading sympy (6.0MiB)
[36m(setup pid=3476)[0m Downloading nvidia-cusparse-cu12 (206.5MiB)
[36m(setup pid=3476)[0m Downloading nvidia-nccl-cu12 (192.0MiB)
[36m(setup pid=3476)[0m Downloading pillow (6.3MiB)
[36m(setup pid=3476)[0m Downloading torch (783.1MiB)
[36m(setup pid=3476)[0m  Downloading nvidia-cufile-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m Verifying transaction: ...working... done
[36m(setup pid=3476)[0m  Downloading torchaudio
[36m(setup pid=3476)[0m  Downloading torchvision
[36m(setup pid=3476)[0m  Downloading pillow
[36m(setup pid=3476)[0m  Downloading nvidia-cuda-cupti-cu12
[36m(setup pid=3476)[0m  Downloading nvidia-nvjitlink-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m Executing transaction: ...working... done
[36m(setup pid=3476)[0m  Downloading nvidia-cuda-nvrtc-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m Error while loading conda entry point: conda-libmamba-solver (module 'libmambapy' has no attribute 'QueryFormat')
[36m(setup pid=2560, ip=10.102.30.168)[0m Using CPython 3.10.12 interpreter at: /usr/bin/python3.10
[36m(setup pid=2560, ip=10.102.30.168)[0m Creating virtual environment with seed packages at: /root/training
[36m(setup pid=2560, ip=10.102.30.168)[0m  + pip==25.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + setuptools==80.9.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + wheel==0.45.1
[36m(setup pid=2560, ip=10.102.30.168)[0m Using Python 3.10.12 environment at: /root/training
[36m(setup pid=2560, ip=10.102.30.168)[0m Resolved 29 packages in 88ms
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading pillow (6.3MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading sympy (6.0MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading torchvision (7.1MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cusparselt-cu12 (149.5MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cufile-cu12 (1.1MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-nvjitlink-cu12 (18.8MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cufft-cu12 (190.9MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cusparse-cu12 (206.5MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-nccl-cu12 (192.0MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cuda-nvrtc-cu12 (22.6MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cusolver-cu12 (150.9MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-curand-cu12 (53.7MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cublas-cu12 (374.9MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cuda-cupti-cu12 (8.5MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading triton (148.4MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading torchaudio (3.3MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading nvidia-cudnn-cu12 (544.5MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading torch (783.1MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cufile-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading torchaudio
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading torchvision
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading pillow
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cuda-cupti-cu12
[36m(setup pid=3476)[0m  Downloading nvidia-curand-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-nvjitlink-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cuda-nvrtc-cu12
[36m(setup pid=3476)[0m  Downloading sympy
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-curand-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading sympy
[36m(setup pid=3476)[0m  Downloading nvidia-cusparselt-cu12
[36m(setup pid=3476)[0m  Downloading nvidia-cusolver-cu12
[36m(setup pid=3476)[0m  Downloading triton
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cusolver-cu12
[36m(setup pid=3476)[0m  Downloading nvidia-nccl-cu12
[36m(setup pid=3476)[0m  Downloading nvidia-cufft-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cusparselt-cu12
[36m(setup pid=3476)[0m  Downloading nvidia-cusparse-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading triton
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cufft-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-nccl-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cusparse-cu12
[36m(setup pid=3476)[0m  Downloading nvidia-cublas-cu12
[36m(setup pid=3476)[0m  Downloading nvidia-cudnn-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cublas-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading nvidia-cudnn-cu12
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading torch
[36m(setup pid=2560, ip=10.102.30.168)[0m Prepared 22 packages in 20.32s
[36m(setup pid=3476)[0m  Downloading torch
[36m(setup pid=3476)[0m Prepared 22 packages in 22.93s
[36m(setup pid=2560, ip=10.102.30.168)[0m Installed 28 packages in 154ms
[36m(setup pid=2560, ip=10.102.30.168)[0m  + filelock==3.18.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + fsspec==2025.7.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + jinja2==3.1.6
[36m(setup pid=2560, ip=10.102.30.168)[0m  + markupsafe==3.0.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + mpmath==1.3.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + networkx==3.4.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + numpy==2.2.6
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cublas-cu12==12.6.4.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cuda-cupti-cu12==12.6.80
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cuda-nvrtc-cu12==12.6.77
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cuda-runtime-cu12==12.6.77
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cudnn-cu12==9.5.1.17
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cufft-cu12==11.3.0.4
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cufile-cu12==1.11.1.6
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-curand-cu12==10.3.7.77
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cusolver-cu12==11.7.1.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cusparse-cu12==12.5.4.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-cusparselt-cu12==0.6.3
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-nccl-cu12==2.26.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-nvjitlink-cu12==12.6.85
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-nvtx-cu12==12.6.77
[36m(setup pid=2560, ip=10.102.30.168)[0m  + pillow==11.3.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + sympy==1.14.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + torch==2.7.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + torchaudio==2.7.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + torchvision==0.22.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + triton==3.3.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + typing-extensions==4.14.1
[36m(setup pid=2560, ip=10.102.30.168)[0m Using Python 3.10.12 environment at: /root/training
[36m(setup pid=3476)[0m Installed 28 packages in 238ms
[36m(setup pid=3476)[0m  + filelock==3.18.0
[36m(setup pid=3476)[0m  + fsspec==2025.7.0
[36m(setup pid=3476)[0m  + jinja2==3.1.6
[36m(setup pid=3476)[0m  + markupsafe==3.0.2
[36m(setup pid=3476)[0m  + mpmath==1.3.0
[36m(setup pid=3476)[0m  + networkx==3.4.2
[36m(setup pid=3476)[0m  + numpy==2.2.6
[36m(setup pid=3476)[0m  + nvidia-cublas-cu12==12.6.4.1
[36m(setup pid=3476)[0m  + nvidia-cuda-cupti-cu12==12.6.80
[36m(setup pid=3476)[0m  + nvidia-cuda-nvrtc-cu12==12.6.77
[36m(setup pid=3476)[0m  + nvidia-cuda-runtime-cu12==12.6.77
[36m(setup pid=3476)[0m  + nvidia-cudnn-cu12==9.5.1.17
[36m(setup pid=3476)[0m  + nvidia-cufft-cu12==11.3.0.4
[36m(setup pid=3476)[0m  + nvidia-cufile-cu12==1.11.1.6
[36m(setup pid=3476)[0m  + nvidia-curand-cu12==10.3.7.77
[36m(setup pid=3476)[0m  + nvidia-cusolver-cu12==11.7.1.2
[36m(setup pid=3476)[0m  + nvidia-cusparse-cu12==12.5.4.2
[36m(setup pid=3476)[0m  + nvidia-cusparselt-cu12==0.6.3
[36m(setup pid=3476)[0m  + nvidia-nccl-cu12==2.26.2
[36m(setup pid=3476)[0m  + nvidia-nvjitlink-cu12==12.6.85
[36m(setup pid=3476)[0m  + nvidia-nvtx-cu12==12.6.77
[36m(setup pid=3476)[0m  + pillow==11.3.0
[36m(setup pid=3476)[0m  + sympy==1.14.0
[36m(setup pid=3476)[0m  + torch==2.7.1
[36m(setup pid=3476)[0m  + torchaudio==2.7.1
[36m(setup pid=3476)[0m  + torchvision==0.22.1
[36m(setup pid=3476)[0m  + triton==3.3.1
[36m(setup pid=3476)[0m  + typing-extensions==4.14.1
[36m(setup pid=3476)[0m Using Python 3.10.12 environment at: /root/training
[36m(setup pid=2560, ip=10.102.30.168)[0m Resolved 73 packages in 311ms
[36m(setup pid=2560, ip=10.102.30.168)[0m    Building deepspeed==0.17.4
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading transformers (10.7MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading tokenizers (3.0MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading pyarrow (40.8MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m Downloading hf-xet (3.0MiB)
[36m(setup pid=3476)[0m Resolved 73 packages in 271ms
[36m(setup pid=3476)[0m    Building deepspeed==0.17.4
[36m(setup pid=3476)[0m Downloading tokenizers (3.0MiB)
[36m(setup pid=3476)[0m Downloading hf-xet (3.0MiB)
[36m(setup pid=3476)[0m Downloading transformers (10.7MiB)
[36m(setup pid=3476)[0m Downloading pyarrow (40.8MiB)
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading tokenizers
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading hf-xet
[36m(setup pid=3476)[0m  Downloading tokenizers
[36m(setup pid=3476)[0m  Downloading hf-xet
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading pyarrow
[36m(setup pid=3476)[0m  Downloading pyarrow
[36m(setup pid=2560, ip=10.102.30.168)[0m  Downloading transformers
[36m(setup pid=3476)[0m  Downloading transformers
[36m(setup pid=2560, ip=10.102.30.168)[0m       Built deepspeed==0.17.4
[36m(setup pid=2560, ip=10.102.30.168)[0m Prepared 21 packages in 1.31s
[36m(setup pid=2560, ip=10.102.30.168)[0m Uninstalled 1 package in 0.84ms
[36m(setup pid=2560, ip=10.102.30.168)[0m Installed 48 packages in 55ms
[36m(setup pid=2560, ip=10.102.30.168)[0m  + accelerate==1.9.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + aiohappyeyeballs==2.6.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + aiohttp==3.12.15
[36m(setup pid=2560, ip=10.102.30.168)[0m  + aiosignal==1.4.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + annotated-types==0.7.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + async-timeout==5.0.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + attrs==25.3.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + certifi==2025.8.3
[36m(setup pid=2560, ip=10.102.30.168)[0m  + charset-normalizer==3.4.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + datasets==4.0.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + deepspeed==0.17.4
[36m(setup pid=2560, ip=10.102.30.168)[0m  + dill==0.3.8
[36m(setup pid=2560, ip=10.102.30.168)[0m  + einops==0.8.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + frozenlist==1.7.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  - fsspec==2025.7.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + fsspec==2025.3.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + hf-xet==1.1.5
[36m(setup pid=2560, ip=10.102.30.168)[0m  + hjson==3.1.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + huggingface-hub==0.34.3
[36m(setup pid=2560, ip=10.102.30.168)[0m  + idna==3.10
[36m(setup pid=2560, ip=10.102.30.168)[0m  + liger-kernel==0.6.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + msgpack==1.1.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + multidict==6.6.3
[36m(setup pid=2560, ip=10.102.30.168)[0m  + multiprocess==0.70.16
[36m(setup pid=2560, ip=10.102.30.168)[0m  + ninja==1.11.1.4
[36m(setup pid=2560, ip=10.102.30.168)[0m  + packaging==25.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + pandas==2.3.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + propcache==0.3.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + psutil==7.0.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + py-cpuinfo==9.0.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + pyarrow==21.0.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + pydantic==2.11.7
[36m(setup pid=2560, ip=10.102.30.168)[0m  + pydantic-core==2.33.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + python-dateutil==2.9.0.post0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + pytz==2025.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + pyyaml==6.0.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + regex==2025.7.34
[36m(setup pid=2560, ip=10.102.30.168)[0m  + requests==2.32.4
[36m(setup pid=2560, ip=10.102.30.168)[0m  + safetensors==0.5.3
[36m(setup pid=2560, ip=10.102.30.168)[0m  + six==1.17.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + tokenizers==0.21.4
[36m(setup pid=2560, ip=10.102.30.168)[0m  + tqdm==4.67.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + transformers==4.54.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + trl==0.20.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + typing-inspection==0.4.1
[36m(setup pid=2560, ip=10.102.30.168)[0m  + tzdata==2025.2
[36m(setup pid=2560, ip=10.102.30.168)[0m  + urllib3==2.5.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + xxhash==3.5.0
[36m(setup pid=2560, ip=10.102.30.168)[0m  + yarl==1.20.1
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m WARNING: apt does not have a stable CLI interface. Use with caution in scripts.
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=3476)[0m       Built deepspeed==0.17.4
[36m(setup pid=3476)[0m Prepared 21 packages in 1.44s
[36m(setup pid=3476)[0m Uninstalled 1 package in 0.91ms
[36m(setup pid=3476)[0m Installed 48 packages in 68ms
[36m(setup pid=3476)[0m  + accelerate==1.9.0
[36m(setup pid=3476)[0m  + aiohappyeyeballs==2.6.1
[36m(setup pid=3476)[0m  + aiohttp==3.12.15
[36m(setup pid=3476)[0m  + aiosignal==1.4.0
[36m(setup pid=3476)[0m  + annotated-types==0.7.0
[36m(setup pid=3476)[0m  + async-timeout==5.0.1
[36m(setup pid=3476)[0m  + attrs==25.3.0
[36m(setup pid=3476)[0m  + certifi==2025.8.3
[36m(setup pid=3476)[0m  + charset-normalizer==3.4.2
[36m(setup pid=3476)[0m  + datasets==4.0.0
[36m(setup pid=3476)[0m  + deepspeed==0.17.4
[36m(setup pid=3476)[0m  + dill==0.3.8
[36m(setup pid=3476)[0m  + einops==0.8.1
[36m(setup pid=3476)[0m  + frozenlist==1.7.0
[36m(setup pid=3476)[0m  - fsspec==2025.7.0
[36m(setup pid=3476)[0m  + fsspec==2025.3.0
[36m(setup pid=3476)[0m  + hf-xet==1.1.5
[36m(setup pid=3476)[0m  + hjson==3.1.0
[36m(setup pid=3476)[0m  + huggingface-hub==0.34.3
[36m(setup pid=3476)[0m  + idna==3.10
[36m(setup pid=3476)[0m  + liger-kernel==0.6.1
[36m(setup pid=3476)[0m  + msgpack==1.1.1
[36m(setup pid=3476)[0m  + multidict==6.6.3
[36m(setup pid=3476)[0m  + multiprocess==0.70.16
[36m(setup pid=3476)[0m  + ninja==1.11.1.4
[36m(setup pid=3476)[0m  + packaging==25.0
[36m(setup pid=3476)[0m  + pandas==2.3.1
[36m(setup pid=3476)[0m  + propcache==0.3.2
[36m(setup pid=3476)[0m  + psutil==7.0.0
[36m(setup pid=3476)[0m  + py-cpuinfo==9.0.0
[36m(setup pid=3476)[0m  + pyarrow==21.0.0
[36m(setup pid=3476)[0m  + pydantic==2.11.7
[36m(setup pid=3476)[0m  + pydantic-core==2.33.2
[36m(setup pid=3476)[0m  + python-dateutil==2.9.0.post0
[36m(setup pid=3476)[0m  + pytz==2025.2
[36m(setup pid=3476)[0m  + pyyaml==6.0.2
[36m(setup pid=3476)[0m  + regex==2025.7.34
[36m(setup pid=3476)[0m  + requests==2.32.4
[36m(setup pid=3476)[0m  + safetensors==0.5.3
[36m(setup pid=3476)[0m  + six==1.17.0
[36m(setup pid=3476)[0m  + tokenizers==0.21.4
[36m(setup pid=3476)[0m  + tqdm==4.67.1
[36m(setup pid=3476)[0m  + transformers==4.54.1
[36m(setup pid=3476)[0m  + trl==0.20.0
[36m(setup pid=3476)[0m  + typing-inspection==0.4.1
[36m(setup pid=3476)[0m  + tzdata==2025.2
[36m(setup pid=3476)[0m  + urllib3==2.5.0
[36m(setup pid=3476)[0m  + xxhash==3.5.0
[36m(setup pid=3476)[0m  + yarl==1.20.1
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m WARNING: apt does not have a stable CLI interface. Use with caution in scripts.
[36m(setup pid=3476)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m Reading package lists...
[36m(setup pid=2560, ip=10.102.30.168)[0m Building dependency tree...
[36m(setup pid=2560, ip=10.102.30.168)[0m Reading state information...
[36m(setup pid=2560, ip=10.102.30.168)[0m The following package was automatically installed and is no longer required:
[36m(setup pid=2560, ip=10.102.30.168)[0m   libfuse2
[36m(setup pid=2560, ip=10.102.30.168)[0m Use 'apt autoremove' to remove it.
[36m(setup pid=2560, ip=10.102.30.168)[0m The following additional packages will be installed:
[36m(setup pid=2560, ip=10.102.30.168)[0m   vim-common vim-runtime
[36m(setup pid=2560, ip=10.102.30.168)[0m Suggested packages:
[36m(setup pid=2560, ip=10.102.30.168)[0m   ctags vim-doc vim-scripts
[36m(setup pid=2560, ip=10.102.30.168)[0m The following NEW packages will be installed:
[36m(setup pid=2560, ip=10.102.30.168)[0m   vmtouch
[36m(setup pid=2560, ip=10.102.30.168)[0m The following packages will be upgraded:
[36m(setup pid=2560, ip=10.102.30.168)[0m   vim vim-common vim-runtime
[36m(setup pid=3476)[0m Reading package lists...
[36m(setup pid=3476)[0m Building dependency tree...
[36m(setup pid=3476)[0m Reading state information...
[36m(setup pid=2560, ip=10.102.30.168)[0m 3 upgraded, 1 newly installed, 0 to remove and 84 not upgraded.
[36m(setup pid=2560, ip=10.102.30.168)[0m Need to get 8664 kB of archives.
[36m(setup pid=2560, ip=10.102.30.168)[0m After this operation, 68.6 kB of additional disk space will be used.
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:1 http://archive.ubuntu.com/ubuntu jammy/universe amd64 vmtouch amd64 1.3.1-2 [21.5 kB]
[36m(setup pid=3476)[0m The following package was automatically installed and is no longer required:
[36m(setup pid=3476)[0m   libfuse2
[36m(setup pid=3476)[0m Use 'apt autoremove' to remove it.
[36m(setup pid=3476)[0m The following additional packages will be installed:
[36m(setup pid=3476)[0m   vim-common vim-runtime
[36m(setup pid=3476)[0m Suggested packages:
[36m(setup pid=3476)[0m   ctags vim-doc vim-scripts
[36m(setup pid=3476)[0m The following NEW packages will be installed:
[36m(setup pid=3476)[0m   vmtouch
[36m(setup pid=3476)[0m The following packages will be upgraded:
[36m(setup pid=3476)[0m   vim vim-common vim-runtime
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:2 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 vim amd64 2:8.2.3995-1ubuntu2.24 [1728 kB]
[36m(setup pid=3476)[0m 3 upgraded, 1 newly installed, 0 to remove and 84 not upgraded.
[36m(setup pid=3476)[0m Need to get 8664 kB of archives.
[36m(setup pid=3476)[0m After this operation, 68.6 kB of additional disk space will be used.
[36m(setup pid=3476)[0m Get:1 http://archive.ubuntu.com/ubuntu jammy/universe amd64 vmtouch amd64 1.3.1-2 [21.5 kB]
[36m(setup pid=3476)[0m Get:2 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 vim amd64 2:8.2.3995-1ubuntu2.24 [1728 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:3 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 vim-runtime all 2:8.2.3995-1ubuntu2.24 [6833 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:4 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 vim-common all 2:8.2.3995-1ubuntu2.24 [81.5 kB]
[36m(setup pid=3476)[0m Get:3 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 vim-runtime all 2:8.2.3995-1ubuntu2.24 [6833 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m debconf: delaying package configuration, since apt-utils is not installed
[36m(setup pid=2560, ip=10.102.30.168)[0m Fetched 8664 kB in 1s (6363 kB/s)
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package vmtouch.
[36m(setup pid=2560, ip=10.102.30.168)[0m (Reading database ... 
(Reading database ... 5%
(Reading database ... 10%
(Reading database ... 15%
(Reading database ... 20%
(Reading database ... 25%
(Reading database ... 30%
(Reading database ... 35%
(Reading database ... 40%
(Reading database ... 45%
(Reading database ... 50%
(Reading database ... 55%
(Reading database ... 60%
(Reading database ... 65%
(Reading database ... 70%
(Reading database ... 75%
(Reading database ... 80%
(Reading database ... 85%
(Reading database ... 90%
(Reading database ... 95%
(Reading database ... 100%
(Reading database ... 23408 files and directories currently installed.)
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../vmtouch_1.3.1-2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking vmtouch (1.3.1-2) ...
[36m(setup pid=3476)[0m Get:4 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 vim-common all 2:8.2.3995-1ubuntu2.24 [81.5 kB]
[36m(setup pid=3476)[0m debconf: delaying package configuration, since apt-utils is not installed
[36m(setup pid=3476)[0m Fetched 8664 kB in 1s (6700 kB/s)
[36m(setup pid=3476)[0m Selecting previously unselected package vmtouch.
[36m(setup pid=3476)[0m (Reading database ... 
(Reading database ... 5%
(Reading database ... 10%
(Reading database ... 15%
(Reading database ... 20%
(Reading database ... 25%
(Reading database ... 30%
(Reading database ... 35%
(Reading database ... 40%
(Reading database ... 45%
(Reading database ... 50%
(Reading database ... 55%
(Reading database ... 60%
(Reading database ... 65%
(Reading database ... 70%
(Reading database ... 75%
(Reading database ... 80%
(Reading database ... 85%
(Reading database ... 90%
(Reading database ... 95%
(Reading database ... 100%
(Reading database ... 23408 files and directories currently installed.)
[36m(setup pid=3476)[0m Preparing to unpack .../vmtouch_1.3.1-2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking vmtouch (1.3.1-2) ...
[36m(setup pid=3476)[0m Preparing to unpack .../vim_2%3a8.2.3995-1ubuntu2.24_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../vim_2%3a8.2.3995-1ubuntu2.24_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking vim (2:8.2.3995-1ubuntu2.24) over (2:8.2.3995-1ubuntu2.21) ...
[36m(setup pid=3476)[0m Preparing to unpack .../vim-runtime_2%3a8.2.3995-1ubuntu2.24_all.deb ...
[36m(setup pid=3476)[0m Unpacking vim-runtime (2:8.2.3995-1ubuntu2.24) over (2:8.2.3995-1ubuntu2.21) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking vim (2:8.2.3995-1ubuntu2.24) over (2:8.2.3995-1ubuntu2.21) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../vim-runtime_2%3a8.2.3995-1ubuntu2.24_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking vim-runtime (2:8.2.3995-1ubuntu2.24) over (2:8.2.3995-1ubuntu2.21) ...
[36m(setup pid=3476)[0m Preparing to unpack .../vim-common_2%3a8.2.3995-1ubuntu2.24_all.deb ...
[36m(setup pid=3476)[0m Unpacking vim-common (2:8.2.3995-1ubuntu2.24) over (2:8.2.3995-1ubuntu2.21) ...
[36m(setup pid=3476)[0m Setting up vmtouch (1.3.1-2) ...
[36m(setup pid=3476)[0m invoke-rc.d: could not determine current runlevel
[36m(setup pid=3476)[0m invoke-rc.d: policy-rc.d denied execution of start.
[36m(setup pid=3476)[0m Setting up vim-common (2:8.2.3995-1ubuntu2.24) ...
[36m(setup pid=3476)[0m Setting up vim-runtime (2:8.2.3995-1ubuntu2.24) ...
[36m(setup pid=3476)[0m Setting up vim (2:8.2.3995-1ubuntu2.24) ...
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m WARNING: apt does not have a stable CLI interface. Use with caution in scripts.
[36m(setup pid=3476)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../vim-common_2%3a8.2.3995-1ubuntu2.24_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking vim-common (2:8.2.3995-1ubuntu2.24) over (2:8.2.3995-1ubuntu2.21) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up vmtouch (1.3.1-2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m invoke-rc.d: could not determine current runlevel
[36m(setup pid=2560, ip=10.102.30.168)[0m invoke-rc.d: policy-rc.d denied execution of start.
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up vim-common (2:8.2.3995-1ubuntu2.24) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up vim-runtime (2:8.2.3995-1ubuntu2.24) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up vim (2:8.2.3995-1ubuntu2.24) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m WARNING: apt does not have a stable CLI interface. Use with caution in scripts.
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=3476)[0m Reading package lists...
[36m(setup pid=3476)[0m Building dependency tree...
[36m(setup pid=3476)[0m Reading state information...
[36m(setup pid=3476)[0m build-essential is already the newest version (12.9ubuntu3).
[36m(setup pid=3476)[0m The following package was automatically installed and is no longer required:
[36m(setup pid=3476)[0m   libfuse2
[36m(setup pid=3476)[0m Use 'apt autoremove' to remove it.
[36m(setup pid=3476)[0m The following additional packages will be installed:
[36m(setup pid=3476)[0m   javascript-common libexpat1 libexpat1-dev libjs-jquery libjs-sphinxdoc
[36m(setup pid=3476)[0m   libjs-underscore libpython3-dev libpython3.10 libpython3.10-dev
[36m(setup pid=3476)[0m   libpython3.10-minimal libpython3.10-stdlib python3-distutils python3-lib2to3
[36m(setup pid=3476)[0m   python3.10 python3.10-minimal
[36m(setup pid=3476)[0m Suggested packages:
[36m(setup pid=3476)[0m   apache2 | lighttpd | httpd python3.10-venv python3.10-doc binfmt-support
[36m(setup pid=3476)[0m The following NEW packages will be installed:
[36m(setup pid=3476)[0m   javascript-common libexpat1-dev libjs-jquery libjs-sphinxdoc
[36m(setup pid=3476)[0m   libjs-underscore libpython3-dev libpython3.10-dev python3-dev
[36m(setup pid=3476)[0m   python3-distutils python3-lib2to3 python3.10-dev
[36m(setup pid=3476)[0m The following packages will be upgraded:
[36m(setup pid=3476)[0m   libexpat1 libpython3.10 libpython3.10-minimal libpython3.10-stdlib
[36m(setup pid=3476)[0m   python3.10 python3.10-minimal
[36m(setup pid=2560, ip=10.102.30.168)[0m Reading package lists...
[36m(setup pid=3476)[0m 6 upgraded, 11 newly installed, 0 to remove and 78 not upgraded.
[36m(setup pid=3476)[0m Need to get 13.7 MB of archives.
[36m(setup pid=3476)[0m After this operation, 25.1 MB of additional disk space will be used.
[36m(setup pid=3476)[0m Get:1 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libexpat1 amd64 2.4.7-1ubuntu0.6 [92.1 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Building dependency tree...
[36m(setup pid=2560, ip=10.102.30.168)[0m Reading state information...
[36m(setup pid=2560, ip=10.102.30.168)[0m build-essential is already the newest version (12.9ubuntu3).
[36m(setup pid=2560, ip=10.102.30.168)[0m The following package was automatically installed and is no longer required:
[36m(setup pid=2560, ip=10.102.30.168)[0m   libfuse2
[36m(setup pid=2560, ip=10.102.30.168)[0m Use 'apt autoremove' to remove it.
[36m(setup pid=2560, ip=10.102.30.168)[0m The following additional packages will be installed:
[36m(setup pid=2560, ip=10.102.30.168)[0m   javascript-common libexpat1 libexpat1-dev libjs-jquery libjs-sphinxdoc
[36m(setup pid=2560, ip=10.102.30.168)[0m   libjs-underscore libpython3-dev libpython3.10 libpython3.10-dev
[36m(setup pid=2560, ip=10.102.30.168)[0m   libpython3.10-minimal libpython3.10-stdlib python3-distutils python3-lib2to3
[36m(setup pid=2560, ip=10.102.30.168)[0m   python3.10 python3.10-minimal
[36m(setup pid=2560, ip=10.102.30.168)[0m Suggested packages:
[36m(setup pid=2560, ip=10.102.30.168)[0m   apache2 | lighttpd | httpd python3.10-venv python3.10-doc binfmt-support
[36m(setup pid=2560, ip=10.102.30.168)[0m The following NEW packages will be installed:
[36m(setup pid=2560, ip=10.102.30.168)[0m   javascript-common libexpat1-dev libjs-jquery libjs-sphinxdoc
[36m(setup pid=2560, ip=10.102.30.168)[0m   libjs-underscore libpython3-dev libpython3.10-dev python3-dev
[36m(setup pid=2560, ip=10.102.30.168)[0m   python3-distutils python3-lib2to3 python3.10-dev
[36m(setup pid=2560, ip=10.102.30.168)[0m The following packages will be upgraded:
[36m(setup pid=2560, ip=10.102.30.168)[0m   libexpat1 libpython3.10 libpython3.10-minimal libpython3.10-stdlib
[36m(setup pid=2560, ip=10.102.30.168)[0m   python3.10 python3.10-minimal
[36m(setup pid=3476)[0m Get:2 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3.10 amd64 3.10.12-1~22.04.10 [1950 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m 6 upgraded, 11 newly installed, 0 to remove and 78 not upgraded.
[36m(setup pid=2560, ip=10.102.30.168)[0m Need to get 13.7 MB of archives.
[36m(setup pid=2560, ip=10.102.30.168)[0m After this operation, 25.1 MB of additional disk space will be used.
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:1 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libexpat1 amd64 2.4.7-1ubuntu0.6 [92.1 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:2 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3.10 amd64 3.10.12-1~22.04.10 [1950 kB]
[36m(setup pid=3476)[0m Get:3 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3.10 amd64 3.10.12-1~22.04.10 [508 kB]
[36m(setup pid=3476)[0m Get:4 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3.10-stdlib amd64 3.10.12-1~22.04.10 [1850 kB]
[36m(setup pid=3476)[0m Get:5 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3.10-minimal amd64 3.10.12-1~22.04.10 [2277 kB]
[36m(setup pid=3476)[0m Get:6 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3.10-minimal amd64 3.10.12-1~22.04.10 [815 kB]
[36m(setup pid=3476)[0m Get:7 http://archive.ubuntu.com/ubuntu jammy/main amd64 javascript-common all 11+nmu1 [5936 B]
[36m(setup pid=3476)[0m Get:8 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libexpat1-dev amd64 2.4.7-1ubuntu0.6 [148 kB]
[36m(setup pid=3476)[0m Get:9 http://archive.ubuntu.com/ubuntu jammy/main amd64 libjs-jquery all 3.6.0+dfsg+~3.5.13-1 [321 kB]
[36m(setup pid=3476)[0m Get:10 http://archive.ubuntu.com/ubuntu jammy/main amd64 libjs-underscore all 1.13.2~dfsg-2 [118 kB]
[36m(setup pid=3476)[0m Get:11 http://archive.ubuntu.com/ubuntu jammy/main amd64 libjs-sphinxdoc all 4.3.2-1 [139 kB]
[36m(setup pid=3476)[0m Get:12 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3.10-dev amd64 3.10.12-1~22.04.10 [4763 kB]
[36m(setup pid=3476)[0m Get:13 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3-dev amd64 3.10.6-1~22.04.1 [7064 B]
[36m(setup pid=3476)[0m Get:14 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3.10-dev amd64 3.10.12-1~22.04.10 [508 kB]
[36m(setup pid=3476)[0m Get:15 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3-lib2to3 all 3.10.8-1~22.04 [77.6 kB]
[36m(setup pid=3476)[0m Get:16 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3-distutils all 3.10.8-1~22.04 [139 kB]
[36m(setup pid=3476)[0m Get:17 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3-dev amd64 3.10.6-1~22.04.1 [26.0 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:3 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3.10 amd64 3.10.12-1~22.04.10 [508 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:4 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3.10-stdlib amd64 3.10.12-1~22.04.10 [1850 kB]
[36m(setup pid=3476)[0m debconf: delaying package configuration, since apt-utils is not installed
[36m(setup pid=3476)[0m Fetched 13.7 MB in 2s (9147 kB/s)
[36m(setup pid=3476)[0m (Reading database ... 
(Reading database ... 5%
(Reading database ... 10%
(Reading database ... 15%
(Reading database ... 20%
(Reading database ... 25%
(Reading database ... 30%
(Reading database ... 35%
(Reading database ... 40%
(Reading database ... 45%
(Reading database ... 50%
(Reading database ... 55%
[36m(setup pid=3476)[0m (Reading database ... 60%
[36m(setup pid=3476)[0m (Reading database ... 65%
(Reading database ... 70%
(Reading database ... 75%
[36m(setup pid=3476)[0m (Reading database ... 80%
(Reading database ... 85%
[36m(setup pid=3476)[0m (Reading database ... 90%
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:5 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3.10-minimal amd64 3.10.12-1~22.04.10 [2277 kB]
[36m(setup pid=3476)[0m (Reading database ... 95%
(Reading database ... 100%
(Reading database ... 23418 files and directories currently installed.)
[36m(setup pid=3476)[0m Preparing to unpack .../00-libexpat1_2.4.7-1ubuntu0.6_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libexpat1:amd64 (2.4.7-1ubuntu0.6) over (2.4.7-1ubuntu0.4) ...
[36m(setup pid=3476)[0m Preparing to unpack .../01-libpython3.10_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libpython3.10:amd64 (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=3476)[0m Preparing to unpack .../02-python3.10_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:6 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3.10-minimal amd64 3.10.12-1~22.04.10 [815 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:7 http://archive.ubuntu.com/ubuntu jammy/main amd64 javascript-common all 11+nmu1 [5936 B]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:8 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libexpat1-dev amd64 2.4.7-1ubuntu0.6 [148 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:9 http://archive.ubuntu.com/ubuntu jammy/main amd64 libjs-jquery all 3.6.0+dfsg+~3.5.13-1 [321 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:10 http://archive.ubuntu.com/ubuntu jammy/main amd64 libjs-underscore all 1.13.2~dfsg-2 [118 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:11 http://archive.ubuntu.com/ubuntu jammy/main amd64 libjs-sphinxdoc all 4.3.2-1 [139 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:12 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3.10-dev amd64 3.10.12-1~22.04.10 [4763 kB]
[36m(setup pid=3476)[0m Unpacking python3.10 (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=3476)[0m Preparing to unpack .../03-libpython3.10-stdlib_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libpython3.10-stdlib:amd64 (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:13 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpython3-dev amd64 3.10.6-1~22.04.1 [7064 B]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:14 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3.10-dev amd64 3.10.12-1~22.04.10 [508 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:15 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3-lib2to3 all 3.10.8-1~22.04 [77.6 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:16 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3-distutils all 3.10.8-1~22.04 [139 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:17 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3-dev amd64 3.10.6-1~22.04.1 [26.0 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m debconf: delaying package configuration, since apt-utils is not installed
[36m(setup pid=2560, ip=10.102.30.168)[0m Fetched 13.7 MB in 1s (9183 kB/s)
[36m(setup pid=2560, ip=10.102.30.168)[0m (Reading database ... 
(Reading database ... 5%
(Reading database ... 10%
(Reading database ... 15%
(Reading database ... 20%
(Reading database ... 25%
(Reading database ... 30%
(Reading database ... 35%
(Reading database ... 40%
(Reading database ... 45%
(Reading database ... 50%
(Reading database ... 55%
(Reading database ... 60%
(Reading database ... 65%
(Reading database ... 70%
(Reading database ... 75%
(Reading database ... 80%
(Reading database ... 85%
(Reading database ... 90%
(Reading database ... 95%
(Reading database ... 100%
(Reading database ... 23418 files and directories currently installed.)
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../00-libexpat1_2.4.7-1ubuntu0.6_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libexpat1:amd64 (2.4.7-1ubuntu0.6) over (2.4.7-1ubuntu0.4) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../01-libpython3.10_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libpython3.10:amd64 (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../02-python3.10_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=3476)[0m Preparing to unpack .../04-python3.10-minimal_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking python3.10-minimal (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking python3.10 (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../03-libpython3.10-stdlib_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libpython3.10-stdlib:amd64 (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=3476)[0m Preparing to unpack .../05-libpython3.10-minimal_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libpython3.10-minimal:amd64 (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=3476)[0m Selecting previously unselected package javascript-common.
[36m(setup pid=3476)[0m Preparing to unpack .../06-javascript-common_11+nmu1_all.deb ...
[36m(setup pid=3476)[0m Unpacking javascript-common (11+nmu1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libexpat1-dev:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../07-libexpat1-dev_2.4.7-1ubuntu0.6_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libexpat1-dev:amd64 (2.4.7-1ubuntu0.6) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libjs-jquery.
[36m(setup pid=3476)[0m Preparing to unpack .../08-libjs-jquery_3.6.0+dfsg+~3.5.13-1_all.deb ...
[36m(setup pid=3476)[0m Unpacking libjs-jquery (3.6.0+dfsg+~3.5.13-1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libjs-underscore.
[36m(setup pid=3476)[0m Preparing to unpack .../09-libjs-underscore_1.13.2~dfsg-2_all.deb ...
[36m(setup pid=3476)[0m Unpacking libjs-underscore (1.13.2~dfsg-2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libjs-sphinxdoc.
[36m(setup pid=3476)[0m Preparing to unpack .../10-libjs-sphinxdoc_4.3.2-1_all.deb ...
[36m(setup pid=3476)[0m Unpacking libjs-sphinxdoc (4.3.2-1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libpython3.10-dev:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../11-libpython3.10-dev_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libpython3.10-dev:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../04-python3.10-minimal_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking python3.10-minimal (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../05-libpython3.10-minimal_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=3476)[0m Selecting previously unselected package libpython3-dev:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../12-libpython3-dev_3.10.6-1~22.04.1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libpython3-dev:amd64 (3.10.6-1~22.04.1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package python3.10-dev.
[36m(setup pid=3476)[0m Preparing to unpack .../13-python3.10-dev_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking python3.10-dev (3.10.12-1~22.04.10) ...
[36m(setup pid=3476)[0m Selecting previously unselected package python3-lib2to3.
[36m(setup pid=3476)[0m Preparing to unpack .../14-python3-lib2to3_3.10.8-1~22.04_all.deb ...
[36m(setup pid=3476)[0m Unpacking python3-lib2to3 (3.10.8-1~22.04) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libpython3.10-minimal:amd64 (3.10.12-1~22.04.10) over (3.10.12-1~22.04.7) ...
[36m(setup pid=3476)[0m Selecting previously unselected package python3-distutils.
[36m(setup pid=3476)[0m Preparing to unpack .../15-python3-distutils_3.10.8-1~22.04_all.deb ...
[36m(setup pid=3476)[0m Unpacking python3-distutils (3.10.8-1~22.04) ...
[36m(setup pid=3476)[0m Selecting previously unselected package python3-dev.
[36m(setup pid=3476)[0m Preparing to unpack .../16-python3-dev_3.10.6-1~22.04.1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking python3-dev (3.10.6-1~22.04.1) ...
[36m(setup pid=3476)[0m Setting up libexpat1:amd64 (2.4.7-1ubuntu0.6) ...
[36m(setup pid=3476)[0m Setting up javascript-common (11+nmu1) ...
[36m(setup pid=3476)[0m Setting up libexpat1-dev:amd64 (2.4.7-1ubuntu0.6) ...
[36m(setup pid=3476)[0m Setting up libpython3.10-minimal:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=3476)[0m Setting up libjs-jquery (3.6.0+dfsg+~3.5.13-1) ...
[36m(setup pid=3476)[0m Setting up python3-lib2to3 (3.10.8-1~22.04) ...
[36m(setup pid=3476)[0m Setting up libjs-underscore (1.13.2~dfsg-2) ...
[36m(setup pid=3476)[0m Setting up python3-distutils (3.10.8-1~22.04) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package javascript-common.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../06-javascript-common_11+nmu1_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking javascript-common (11+nmu1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libexpat1-dev:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../07-libexpat1-dev_2.4.7-1ubuntu0.6_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libexpat1-dev:amd64 (2.4.7-1ubuntu0.6) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libjs-jquery.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../08-libjs-jquery_3.6.0+dfsg+~3.5.13-1_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libjs-jquery (3.6.0+dfsg+~3.5.13-1) ...
[36m(setup pid=3476)[0m Setting up python3.10-minimal (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libjs-underscore.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../09-libjs-underscore_1.13.2~dfsg-2_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libjs-underscore (1.13.2~dfsg-2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libjs-sphinxdoc.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../10-libjs-sphinxdoc_4.3.2-1_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libjs-sphinxdoc (4.3.2-1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libpython3.10-dev:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../11-libpython3.10-dev_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libpython3.10-dev:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libpython3-dev:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../12-libpython3-dev_3.10.6-1~22.04.1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libpython3-dev:amd64 (3.10.6-1~22.04.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package python3.10-dev.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../13-python3.10-dev_3.10.12-1~22.04.10_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking python3.10-dev (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package python3-lib2to3.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../14-python3-lib2to3_3.10.8-1~22.04_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking python3-lib2to3 (3.10.8-1~22.04) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package python3-distutils.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../15-python3-distutils_3.10.8-1~22.04_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking python3-distutils (3.10.8-1~22.04) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package python3-dev.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../16-python3-dev_3.10.6-1~22.04.1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking python3-dev (3.10.6-1~22.04.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libexpat1:amd64 (2.4.7-1ubuntu0.6) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up javascript-common (11+nmu1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libexpat1-dev:amd64 (2.4.7-1ubuntu0.6) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libpython3.10-minimal:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libjs-jquery (3.6.0+dfsg+~3.5.13-1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up python3-lib2to3 (3.10.8-1~22.04) ...
[36m(setup pid=3476)[0m Setting up libpython3.10-stdlib:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=3476)[0m Setting up libjs-sphinxdoc (4.3.2-1) ...
[36m(setup pid=3476)[0m Setting up libpython3.10:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=3476)[0m Setting up python3.10 (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libjs-underscore (1.13.2~dfsg-2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up python3-distutils (3.10.8-1~22.04) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up python3.10-minimal (3.10.12-1~22.04.10) ...
[36m(setup pid=3476)[0m Setting up libpython3.10-dev:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=3476)[0m Setting up python3.10-dev (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libpython3.10-stdlib:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libjs-sphinxdoc (4.3.2-1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libpython3.10:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up python3.10 (3.10.12-1~22.04.10) ...
[36m(setup pid=3476)[0m Setting up libpython3-dev:amd64 (3.10.6-1~22.04.1) ...
[36m(setup pid=3476)[0m Setting up python3-dev (3.10.6-1~22.04.1) ...
[36m(setup pid=3476)[0m Processing triggers for libc-bin (2.35-0ubuntu3.6) ...
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m WARNING: apt does not have a stable CLI interface. Use with caution in scripts.
[36m(setup pid=3476)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libpython3.10-dev:amd64 (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up python3.10-dev (3.10.12-1~22.04.10) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libpython3-dev:amd64 (3.10.6-1~22.04.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up python3-dev (3.10.6-1~22.04.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Processing triggers for libc-bin (2.35-0ubuntu3.6) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m WARNING: apt does not have a stable CLI interface. Use with caution in scripts.
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=3476)[0m Reading package lists...
[36m(setup pid=3476)[0m Building dependency tree...
[36m(setup pid=3476)[0m Reading state information...
[36m(setup pid=3476)[0m infiniband-diags is already the newest version (2410mlnx54-1.2410068).
[36m(setup pid=3476)[0m The following package was automatically installed and is no longer required:
[36m(setup pid=3476)[0m   libfuse2
[36m(setup pid=3476)[0m Use 'apt autoremove' to remove it.
[36m(setup pid=3476)[0m The following additional packages will be installed:
[36m(setup pid=3476)[0m   dbus dmsetup gir1.2-glib-2.0 libapparmor1 libargon2-1 libatm1 libbpf0
[36m(setup pid=3476)[0m   libcryptsetup12 libdbus-1-3 libdevmapper1.02.1 libelf1 libgirepository-1.0-1
[36m(setup pid=3476)[0m   libglib2.0-0 libglib2.0-data libip4tc2 libjson-c5 libmnl0 libnl-genl-3-200
[36m(setup pid=3476)[0m   libsensors-config libsensors5 libsystemd0 libxtables12 networkd-dispatcher
[36m(setup pid=3476)[0m   python3-dbus python3-gi shared-mime-info systemd systemd-timesyncd
[36m(setup pid=3476)[0m   xdg-user-dirs
[36m(setup pid=3476)[0m Suggested packages:
[36m(setup pid=3476)[0m   default-dbus-session-bus | dbus-session-bus lm-sensors lsof strace
[36m(setup pid=3476)[0m   iproute2-doc iw | wireless-tools python-dbus-doc isag systemd-container
[36m(setup pid=3476)[0m   libtss2-esys-3.0.2-0 libtss2-mu0 libtss2-rc0 policykit-1
[36m(setup pid=3476)[0m The following NEW packages will be installed:
[36m(setup pid=3476)[0m   dbus dmsetup gir1.2-glib-2.0 htop iproute2 libapparmor1 libargon2-1 libatm1
[36m(setup pid=3476)[0m   libbpf0 libcryptsetup12 libdbus-1-3 libdevmapper1.02.1 libelf1
[36m(setup pid=3476)[0m   libgirepository-1.0-1 libglib2.0-0 libglib2.0-data libip4tc2 libjson-c5
[36m(setup pid=3476)[0m   libmnl0 libnl-genl-3-200 libsensors-config libsensors5 libxtables12
[36m(setup pid=3476)[0m   networkd-dispatcher python3-dbus python3-gi shared-mime-info sysstat systemd
[36m(setup pid=3476)[0m   systemd-timesyncd xdg-user-dirs
[36m(setup pid=3476)[0m The following packages will be upgraded:
[36m(setup pid=3476)[0m   libsystemd0 net-tools
[36m(setup pid=2560, ip=10.102.30.168)[0m Reading package lists...
[36m(setup pid=2560, ip=10.102.30.168)[0m Building dependency tree...
[36m(setup pid=2560, ip=10.102.30.168)[0m Reading state information...
[36m(setup pid=3476)[0m 2 upgraded, 31 newly installed, 0 to remove and 76 not upgraded.
[36m(setup pid=3476)[0m Need to get 10.6 MB of archives.
[36m(setup pid=3476)[0m After this operation, 35.4 MB of additional disk space will be used.
[36m(setup pid=3476)[0m Get:1 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libsystemd0 amd64 249.11-0ubuntu3.16 [317 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m infiniband-diags is already the newest version (2410mlnx54-1.2410068).
[36m(setup pid=2560, ip=10.102.30.168)[0m The following package was automatically installed and is no longer required:
[36m(setup pid=2560, ip=10.102.30.168)[0m   libfuse2
[36m(setup pid=2560, ip=10.102.30.168)[0m Use 'apt autoremove' to remove it.
[36m(setup pid=2560, ip=10.102.30.168)[0m The following additional packages will be installed:
[36m(setup pid=2560, ip=10.102.30.168)[0m   dbus dmsetup gir1.2-glib-2.0 libapparmor1 libargon2-1 libatm1 libbpf0
[36m(setup pid=2560, ip=10.102.30.168)[0m   libcryptsetup12 libdbus-1-3 libdevmapper1.02.1 libelf1 libgirepository-1.0-1
[36m(setup pid=2560, ip=10.102.30.168)[0m   libglib2.0-0 libglib2.0-data libip4tc2 libjson-c5 libmnl0 libnl-genl-3-200
[36m(setup pid=2560, ip=10.102.30.168)[0m   libsensors-config libsensors5 libsystemd0 libxtables12 networkd-dispatcher
[36m(setup pid=2560, ip=10.102.30.168)[0m   python3-dbus python3-gi shared-mime-info systemd systemd-timesyncd
[36m(setup pid=2560, ip=10.102.30.168)[0m   xdg-user-dirs
[36m(setup pid=2560, ip=10.102.30.168)[0m Suggested packages:
[36m(setup pid=2560, ip=10.102.30.168)[0m   default-dbus-session-bus | dbus-session-bus lm-sensors lsof strace
[36m(setup pid=2560, ip=10.102.30.168)[0m   iproute2-doc iw | wireless-tools python-dbus-doc isag systemd-container
[36m(setup pid=2560, ip=10.102.30.168)[0m   libtss2-esys-3.0.2-0 libtss2-mu0 libtss2-rc0 policykit-1
[36m(setup pid=2560, ip=10.102.30.168)[0m The following NEW packages will be installed:
[36m(setup pid=2560, ip=10.102.30.168)[0m   dbus dmsetup gir1.2-glib-2.0 htop iproute2 libapparmor1 libargon2-1 libatm1
[36m(setup pid=2560, ip=10.102.30.168)[0m   libbpf0 libcryptsetup12 libdbus-1-3 libdevmapper1.02.1 libelf1
[36m(setup pid=2560, ip=10.102.30.168)[0m   libgirepository-1.0-1 libglib2.0-0 libglib2.0-data libip4tc2 libjson-c5
[36m(setup pid=2560, ip=10.102.30.168)[0m   libmnl0 libnl-genl-3-200 libsensors-config libsensors5 libxtables12
[36m(setup pid=2560, ip=10.102.30.168)[0m   networkd-dispatcher python3-dbus python3-gi shared-mime-info sysstat systemd
[36m(setup pid=2560, ip=10.102.30.168)[0m   systemd-timesyncd xdg-user-dirs
[36m(setup pid=2560, ip=10.102.30.168)[0m The following packages will be upgraded:
[36m(setup pid=2560, ip=10.102.30.168)[0m   libsystemd0 net-tools
[36m(setup pid=2560, ip=10.102.30.168)[0m 2 upgraded, 31 newly installed, 0 to remove and 76 not upgraded.
[36m(setup pid=2560, ip=10.102.30.168)[0m Need to get 10.6 MB of archives.
[36m(setup pid=2560, ip=10.102.30.168)[0m After this operation, 35.4 MB of additional disk space will be used.
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:1 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libsystemd0 amd64 249.11-0ubuntu3.16 [317 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:2 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libapparmor1 amd64 3.0.4-2ubuntu2.4 [39.7 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:3 http://archive.ubuntu.com/ubuntu jammy/main amd64 libargon2-1 amd64 0~20171227-0.3 [19.5 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:4 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libdevmapper1.02.1 amd64 2:1.02.175-2.1ubuntu5 [139 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:5 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libjson-c5 amd64 0.15-3~ubuntu1.22.04.2 [33.5 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:6 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libcryptsetup12 amd64 2:2.4.3-1ubuntu1.3 [211 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:7 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libip4tc2 amd64 1.8.7-1ubuntu5.2 [19.9 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:8 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 systemd amd64 249.11-0ubuntu3.16 [4581 kB]
[36m(setup pid=3476)[0m Get:2 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libapparmor1 amd64 3.0.4-2ubuntu2.4 [39.7 kB]
[36m(setup pid=3476)[0m Get:3 http://archive.ubuntu.com/ubuntu jammy/main amd64 libargon2-1 amd64 0~20171227-0.3 [19.5 kB]
[36m(setup pid=3476)[0m Get:4 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libdevmapper1.02.1 amd64 2:1.02.175-2.1ubuntu5 [139 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:9 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libdbus-1-3 amd64 1.12.20-2ubuntu4.1 [189 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:10 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 dbus amd64 1.12.20-2ubuntu4.1 [158 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:11 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 dmsetup amd64 2:1.02.175-2.1ubuntu5 [81.7 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:12 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libglib2.0-0 amd64 2.72.4-0ubuntu2.5 [1466 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:13 http://archive.ubuntu.com/ubuntu jammy/main amd64 libgirepository-1.0-1 amd64 1.72.0-1 [55.6 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:14 http://archive.ubuntu.com/ubuntu jammy/main amd64 gir1.2-glib-2.0 amd64 1.72.0-1 [164 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:15 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libelf1 amd64 0.186-1ubuntu0.1 [51.1 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:16 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libbpf0 amd64 1:0.5.0-1ubuntu22.04.1 [140 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:17 http://archive.ubuntu.com/ubuntu jammy/main amd64 libmnl0 amd64 1.0.4-3build2 [13.2 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:18 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libxtables12 amd64 1.8.7-1ubuntu5.2 [31.3 kB]
[36m(setup pid=3476)[0m Get:5 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libjson-c5 amd64 0.15-3~ubuntu1.22.04.2 [33.5 kB]
[36m(setup pid=3476)[0m Get:6 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libcryptsetup12 amd64 2:2.4.3-1ubuntu1.3 [211 kB]
[36m(setup pid=3476)[0m Get:7 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libip4tc2 amd64 1.8.7-1ubuntu5.2 [19.9 kB]
[36m(setup pid=3476)[0m Get:8 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 systemd amd64 249.11-0ubuntu3.16 [4581 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:19 http://archive.ubuntu.com/ubuntu jammy/main amd64 iproute2 amd64 5.15.0-1ubuntu2 [1070 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:20 http://archive.ubuntu.com/ubuntu jammy/main amd64 libatm1 amd64 1:2.5.1-4build2 [22.8 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:21 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libglib2.0-data all 2.72.4-0ubuntu2.5 [4656 B]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:22 http://archive.ubuntu.com/ubuntu jammy/main amd64 python3-dbus amd64 1.2.18-3build1 [99.5 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:23 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3-gi amd64 3.42.1-0ubuntu1 [229 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:24 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 networkd-dispatcher all 2.1-2ubuntu0.22.04.2 [15.8 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:25 http://archive.ubuntu.com/ubuntu jammy/main amd64 shared-mime-info amd64 2.1-2 [454 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:26 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 systemd-timesyncd amd64 249.11-0ubuntu3.16 [31.2 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:27 http://archive.ubuntu.com/ubuntu jammy/main amd64 xdg-user-dirs amd64 0.17-2ubuntu4 [53.9 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:28 http://archive.ubuntu.com/ubuntu jammy/main amd64 libnl-genl-3-200 amd64 3.5.0-0.1 [12.4 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:29 http://archive.ubuntu.com/ubuntu jammy/main amd64 htop amd64 3.0.5-7build2 [128 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:30 http://archive.ubuntu.com/ubuntu jammy/main amd64 libsensors-config all 1:3.6.0-7ubuntu1 [5274 B]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:31 http://archive.ubuntu.com/ubuntu jammy/main amd64 libsensors5 amd64 1:3.6.0-7ubuntu1 [26.3 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:32 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 net-tools amd64 1.60+git20181103.0eebece-1ubuntu5.4 [204 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Get:33 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 sysstat amd64 12.5.2-2ubuntu0.2 [487 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m debconf: delaying package configuration, since apt-utils is not installed
[36m(setup pid=2560, ip=10.102.30.168)[0m Fetched 10.6 MB in 1s (17.9 MB/s)
[36m(setup pid=2560, ip=10.102.30.168)[0m (Reading database ... 
(Reading database ... 5%
(Reading database ... 10%
(Reading database ... 15%
(Reading database ... 20%
(Reading database ... 25%
(Reading database ... 30%
(Reading database ... 35%
(Reading database ... 40%
(Reading database ... 45%
(Reading database ... 50%
(Reading database ... 55%
(Reading database ... 60%
(Reading database ... 65%
(Reading database ... 70%
(Reading database ... 75%
(Reading database ... 80%
(Reading database ... 85%
(Reading database ... 90%
(Reading database ... 95%
(Reading database ... 100%
(Reading database ... 23994 files and directories currently installed.)
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../libsystemd0_249.11-0ubuntu3.16_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libsystemd0:amd64 (249.11-0ubuntu3.16) over (249.11-0ubuntu3.12) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libsystemd0:amd64 (249.11-0ubuntu3.16) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libapparmor1:amd64.
[36m(setup pid=3476)[0m Get:9 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libdbus-1-3 amd64 1.12.20-2ubuntu4.1 [189 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m (Reading database ... 
(Reading database ... 5%
(Reading database ... 10%
(Reading database ... 15%
(Reading database ... 20%
(Reading database ... 25%
(Reading database ... 30%
(Reading database ... 35%
(Reading database ... 40%
(Reading database ... 45%
(Reading database ... 50%
(Reading database ... 55%
(Reading database ... 60%
(Reading database ... 65%
(Reading database ... 70%
(Reading database ... 75%
(Reading database ... 80%
(Reading database ... 85%
(Reading database ... 90%
(Reading database ... 95%
(Reading database ... 100%
(Reading database ... 23994 files and directories currently installed.)
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../00-libapparmor1_3.0.4-2ubuntu2.4_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libapparmor1:amd64 (3.0.4-2ubuntu2.4) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libargon2-1:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../01-libargon2-1_0~20171227-0.3_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libargon2-1:amd64 (0~20171227-0.3) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libdevmapper1.02.1:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../02-libdevmapper1.02.1_2%3a1.02.175-2.1ubuntu5_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libdevmapper1.02.1:amd64 (2:1.02.175-2.1ubuntu5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libjson-c5:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../03-libjson-c5_0.15-3~ubuntu1.22.04.2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libjson-c5:amd64 (0.15-3~ubuntu1.22.04.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libcryptsetup12:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../04-libcryptsetup12_2%3a2.4.3-1ubuntu1.3_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libcryptsetup12:amd64 (2:2.4.3-1ubuntu1.3) ...
[36m(setup pid=3476)[0m Get:10 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 dbus amd64 1.12.20-2ubuntu4.1 [158 kB]
[36m(setup pid=3476)[0m Get:11 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 dmsetup amd64 2:1.02.175-2.1ubuntu5 [81.7 kB]
[36m(setup pid=3476)[0m Get:12 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libglib2.0-0 amd64 2.72.4-0ubuntu2.5 [1466 kB]
[36m(setup pid=3476)[0m Get:13 http://archive.ubuntu.com/ubuntu jammy/main amd64 libgirepository-1.0-1 amd64 1.72.0-1 [55.6 kB]
[36m(setup pid=3476)[0m Get:14 http://archive.ubuntu.com/ubuntu jammy/main amd64 gir1.2-glib-2.0 amd64 1.72.0-1 [164 kB]
[36m(setup pid=3476)[0m Get:15 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libelf1 amd64 0.186-1ubuntu0.1 [51.1 kB]
[36m(setup pid=3476)[0m Get:16 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libbpf0 amd64 1:0.5.0-1ubuntu22.04.1 [140 kB]
[36m(setup pid=3476)[0m Get:17 http://archive.ubuntu.com/ubuntu jammy/main amd64 libmnl0 amd64 1.0.4-3build2 [13.2 kB]
[36m(setup pid=3476)[0m Get:18 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libxtables12 amd64 1.8.7-1ubuntu5.2 [31.3 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libip4tc2:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../05-libip4tc2_1.8.7-1ubuntu5.2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libip4tc2:amd64 (1.8.7-1ubuntu5.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package systemd.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../06-systemd_249.11-0ubuntu3.16_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking systemd (249.11-0ubuntu3.16) ...
[36m(setup pid=3476)[0m Get:19 http://archive.ubuntu.com/ubuntu jammy/main amd64 iproute2 amd64 5.15.0-1ubuntu2 [1070 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libdbus-1-3:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../07-libdbus-1-3_1.12.20-2ubuntu4.1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libdbus-1-3:amd64 (1.12.20-2ubuntu4.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package dbus.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../08-dbus_1.12.20-2ubuntu4.1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking dbus (1.12.20-2ubuntu4.1) ...
[36m(setup pid=3476)[0m Get:20 http://archive.ubuntu.com/ubuntu jammy/main amd64 libatm1 amd64 1:2.5.1-4build2 [22.8 kB]
[36m(setup pid=3476)[0m Get:21 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 libglib2.0-data all 2.72.4-0ubuntu2.5 [4656 B]
[36m(setup pid=3476)[0m Get:22 http://archive.ubuntu.com/ubuntu jammy/main amd64 python3-dbus amd64 1.2.18-3build1 [99.5 kB]
[36m(setup pid=3476)[0m Get:23 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 python3-gi amd64 3.42.1-0ubuntu1 [229 kB]
[36m(setup pid=3476)[0m Get:24 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 networkd-dispatcher all 2.1-2ubuntu0.22.04.2 [15.8 kB]
[36m(setup pid=3476)[0m Get:25 http://archive.ubuntu.com/ubuntu jammy/main amd64 shared-mime-info amd64 2.1-2 [454 kB]
[36m(setup pid=3476)[0m Get:26 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 systemd-timesyncd amd64 249.11-0ubuntu3.16 [31.2 kB]
[36m(setup pid=3476)[0m Get:27 http://archive.ubuntu.com/ubuntu jammy/main amd64 xdg-user-dirs amd64 0.17-2ubuntu4 [53.9 kB]
[36m(setup pid=3476)[0m Get:28 http://archive.ubuntu.com/ubuntu jammy/main amd64 libnl-genl-3-200 amd64 3.5.0-0.1 [12.4 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package dmsetup.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../09-dmsetup_2%3a1.02.175-2.1ubuntu5_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking dmsetup (2:1.02.175-2.1ubuntu5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libglib2.0-0:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../10-libglib2.0-0_2.72.4-0ubuntu2.5_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libglib2.0-0:amd64 (2.72.4-0ubuntu2.5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libgirepository-1.0-1:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../11-libgirepository-1.0-1_1.72.0-1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libgirepository-1.0-1:amd64 (1.72.0-1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package gir1.2-glib-2.0:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../12-gir1.2-glib-2.0_1.72.0-1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking gir1.2-glib-2.0:amd64 (1.72.0-1) ...
[36m(setup pid=3476)[0m Get:29 http://archive.ubuntu.com/ubuntu jammy/main amd64 htop amd64 3.0.5-7build2 [128 kB]
[36m(setup pid=3476)[0m Get:30 http://archive.ubuntu.com/ubuntu jammy/main amd64 libsensors-config all 1:3.6.0-7ubuntu1 [5274 B]
[36m(setup pid=3476)[0m Get:31 http://archive.ubuntu.com/ubuntu jammy/main amd64 libsensors5 amd64 1:3.6.0-7ubuntu1 [26.3 kB]
[36m(setup pid=3476)[0m Get:32 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 net-tools amd64 1.60+git20181103.0eebece-1ubuntu5.4 [204 kB]
[36m(setup pid=3476)[0m Get:33 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 sysstat amd64 12.5.2-2ubuntu0.2 [487 kB]
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libelf1:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../13-libelf1_0.186-1ubuntu0.1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libelf1:amd64 (0.186-1ubuntu0.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libbpf0:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../14-libbpf0_1%3a0.5.0-1ubuntu22.04.1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libbpf0:amd64 (1:0.5.0-1ubuntu22.04.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libmnl0:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../15-libmnl0_1.0.4-3build2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libmnl0:amd64 (1.0.4-3build2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libxtables12:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../16-libxtables12_1.8.7-1ubuntu5.2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libxtables12:amd64 (1.8.7-1ubuntu5.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package iproute2.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../17-iproute2_5.15.0-1ubuntu2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking iproute2 (5.15.0-1ubuntu2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libatm1:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../18-libatm1_1%3a2.5.1-4build2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libatm1:amd64 (1:2.5.1-4build2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libglib2.0-data.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../19-libglib2.0-data_2.72.4-0ubuntu2.5_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libglib2.0-data (2.72.4-0ubuntu2.5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package python3-dbus.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../20-python3-dbus_1.2.18-3build1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking python3-dbus (1.2.18-3build1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package python3-gi.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../21-python3-gi_3.42.1-0ubuntu1_amd64.deb ...
[36m(setup pid=3476)[0m debconf: delaying package configuration, since apt-utils is not installed
[36m(setup pid=3476)[0m Fetched 10.6 MB in 2s (6357 kB/s)
[36m(setup pid=3476)[0m (Reading database ... 
(Reading database ... 5%
(Reading database ... 10%
(Reading database ... 15%
(Reading database ... 20%
(Reading database ... 25%
(Reading database ... 30%
(Reading database ... 35%
(Reading database ... 40%
(Reading database ... 45%
(Reading database ... 50%
(Reading database ... 55%
(Reading database ... 60%
(Reading database ... 65%
(Reading database ... 70%
(Reading database ... 75%
(Reading database ... 80%
(Reading database ... 85%
(Reading database ... 90%
(Reading database ... 95%
(Reading database ... 100%
(Reading database ... 23994 files and directories currently installed.)
[36m(setup pid=3476)[0m Preparing to unpack .../libsystemd0_249.11-0ubuntu3.16_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libsystemd0:amd64 (249.11-0ubuntu3.16) over (249.11-0ubuntu3.12) ...
[36m(setup pid=3476)[0m Setting up libsystemd0:amd64 (249.11-0ubuntu3.16) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking python3-gi (3.42.1-0ubuntu1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package networkd-dispatcher.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../22-networkd-dispatcher_2.1-2ubuntu0.22.04.2_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking networkd-dispatcher (2.1-2ubuntu0.22.04.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package shared-mime-info.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../23-shared-mime-info_2.1-2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking shared-mime-info (2.1-2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package systemd-timesyncd.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../24-systemd-timesyncd_249.11-0ubuntu3.16_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking systemd-timesyncd (249.11-0ubuntu3.16) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package xdg-user-dirs.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../25-xdg-user-dirs_0.17-2ubuntu4_amd64.deb ...
[36m(setup pid=3476)[0m Selecting previously unselected package libapparmor1:amd64.
[36m(setup pid=3476)[0m (Reading database ... 
(Reading database ... 5%
(Reading database ... 10%
(Reading database ... 15%
(Reading database ... 20%
(Reading database ... 25%
(Reading database ... 30%
(Reading database ... 35%
(Reading database ... 40%
(Reading database ... 45%
(Reading database ... 50%
(Reading database ... 55%
(Reading database ... 60%
(Reading database ... 65%
(Reading database ... 70%
(Reading database ... 75%
(Reading database ... 80%
(Reading database ... 85%
(Reading database ... 90%
(Reading database ... 95%
(Reading database ... 100%
(Reading database ... 23994 files and directories currently installed.)
[36m(setup pid=3476)[0m Preparing to unpack .../00-libapparmor1_3.0.4-2ubuntu2.4_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libapparmor1:amd64 (3.0.4-2ubuntu2.4) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libargon2-1:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../01-libargon2-1_0~20171227-0.3_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libargon2-1:amd64 (0~20171227-0.3) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libdevmapper1.02.1:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../02-libdevmapper1.02.1_2%3a1.02.175-2.1ubuntu5_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libdevmapper1.02.1:amd64 (2:1.02.175-2.1ubuntu5) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libjson-c5:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../03-libjson-c5_0.15-3~ubuntu1.22.04.2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libjson-c5:amd64 (0.15-3~ubuntu1.22.04.2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libcryptsetup12:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../04-libcryptsetup12_2%3a2.4.3-1ubuntu1.3_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libcryptsetup12:amd64 (2:2.4.3-1ubuntu1.3) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking xdg-user-dirs (0.17-2ubuntu4) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libnl-genl-3-200:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../26-libnl-genl-3-200_3.5.0-0.1_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libnl-genl-3-200:amd64 (3.5.0-0.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package htop.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../27-htop_3.0.5-7build2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking htop (3.0.5-7build2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libsensors-config.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../28-libsensors-config_1%3a3.6.0-7ubuntu1_all.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libsensors-config (1:3.6.0-7ubuntu1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package libsensors5:amd64.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../29-libsensors5_1%3a3.6.0-7ubuntu1_amd64.deb ...
[36m(setup pid=3476)[0m Selecting previously unselected package libip4tc2:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../05-libip4tc2_1.8.7-1ubuntu5.2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libip4tc2:amd64 (1.8.7-1ubuntu5.2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package systemd.
[36m(setup pid=3476)[0m Preparing to unpack .../06-systemd_249.11-0ubuntu3.16_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking systemd (249.11-0ubuntu3.16) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking libsensors5:amd64 (1:3.6.0-7ubuntu1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../30-net-tools_1.60+git20181103.0eebece-1ubuntu5.4_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking net-tools (1.60+git20181103.0eebece-1ubuntu5.4) over (1.60+git20181103.0eebece-1ubuntu5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Selecting previously unselected package sysstat.
[36m(setup pid=2560, ip=10.102.30.168)[0m Preparing to unpack .../31-sysstat_12.5.2-2ubuntu0.2_amd64.deb ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Unpacking sysstat (12.5.2-2ubuntu0.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libip4tc2:amd64 (1.8.7-1ubuntu5.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up net-tools (1.60+git20181103.0eebece-1ubuntu5.4) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libapparmor1:amd64 (3.0.4-2ubuntu2.4) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up xdg-user-dirs (0.17-2ubuntu4) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libglib2.0-0:amd64 (2.72.4-0ubuntu2.5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m No schema files found: doing nothing.
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libargon2-1:amd64 (0~20171227-0.3) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libsensors-config (1:3.6.0-7ubuntu1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libatm1:amd64 (1:2.5.1-4build2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libglib2.0-data (2.72.4-0ubuntu2.5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libdbus-1-3:amd64 (1.12.20-2ubuntu4.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up dbus (1.12.20-2ubuntu4.1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libdbus-1-3:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../07-libdbus-1-3_1.12.20-2ubuntu4.1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libdbus-1-3:amd64 (1.12.20-2ubuntu4.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up shared-mime-info (2.1-2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package dbus.
[36m(setup pid=3476)[0m Preparing to unpack .../08-dbus_1.12.20-2ubuntu4.1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking dbus (1.12.20-2ubuntu4.1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package dmsetup.
[36m(setup pid=3476)[0m Preparing to unpack .../09-dmsetup_2%3a1.02.175-2.1ubuntu5_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking dmsetup (2:1.02.175-2.1ubuntu5) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libglib2.0-0:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../10-libglib2.0-0_2.72.4-0ubuntu2.5_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libglib2.0-0:amd64 (2.72.4-0ubuntu2.5) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libgirepository-1.0-1:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../11-libgirepository-1.0-1_1.72.0-1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libgirepository-1.0-1:amd64 (1.72.0-1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package gir1.2-glib-2.0:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../12-gir1.2-glib-2.0_1.72.0-1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking gir1.2-glib-2.0:amd64 (1.72.0-1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libelf1:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../13-libelf1_0.186-1ubuntu0.1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libelf1:amd64 (0.186-1ubuntu0.1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libbpf0:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../14-libbpf0_1%3a0.5.0-1ubuntu22.04.1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libbpf0:amd64 (1:0.5.0-1ubuntu22.04.1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libmnl0:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../15-libmnl0_1.0.4-3build2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libmnl0:amd64 (1.0.4-3build2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libxtables12:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../16-libxtables12_1.8.7-1ubuntu5.2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libxtables12:amd64 (1.8.7-1ubuntu5.2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package iproute2.
[36m(setup pid=3476)[0m Preparing to unpack .../17-iproute2_5.15.0-1ubuntu2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking iproute2 (5.15.0-1ubuntu2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libatm1:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../18-libatm1_1%3a2.5.1-4build2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libatm1:amd64 (1:2.5.1-4build2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libglib2.0-data.
[36m(setup pid=3476)[0m Preparing to unpack .../19-libglib2.0-data_2.72.4-0ubuntu2.5_all.deb ...
[36m(setup pid=3476)[0m Unpacking libglib2.0-data (2.72.4-0ubuntu2.5) ...
[36m(setup pid=3476)[0m Selecting previously unselected package python3-dbus.
[36m(setup pid=3476)[0m Preparing to unpack .../20-python3-dbus_1.2.18-3build1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking python3-dbus (1.2.18-3build1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package python3-gi.
[36m(setup pid=3476)[0m Preparing to unpack .../21-python3-gi_3.42.1-0ubuntu1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking python3-gi (3.42.1-0ubuntu1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package networkd-dispatcher.
[36m(setup pid=3476)[0m Preparing to unpack .../22-networkd-dispatcher_2.1-2ubuntu0.22.04.2_all.deb ...
[36m(setup pid=3476)[0m Unpacking networkd-dispatcher (2.1-2ubuntu0.22.04.2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package shared-mime-info.
[36m(setup pid=3476)[0m Preparing to unpack .../23-shared-mime-info_2.1-2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking shared-mime-info (2.1-2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package systemd-timesyncd.
[36m(setup pid=3476)[0m Preparing to unpack .../24-systemd-timesyncd_249.11-0ubuntu3.16_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking systemd-timesyncd (249.11-0ubuntu3.16) ...
[36m(setup pid=3476)[0m Selecting previously unselected package xdg-user-dirs.
[36m(setup pid=3476)[0m Preparing to unpack .../25-xdg-user-dirs_0.17-2ubuntu4_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking xdg-user-dirs (0.17-2ubuntu4) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libnl-genl-3-200:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../26-libnl-genl-3-200_3.5.0-0.1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libnl-genl-3-200:amd64 (3.5.0-0.1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package htop.
[36m(setup pid=3476)[0m Preparing to unpack .../27-htop_3.0.5-7build2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking htop (3.0.5-7build2) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libsensors-config.
[36m(setup pid=3476)[0m Preparing to unpack .../28-libsensors-config_1%3a3.6.0-7ubuntu1_all.deb ...
[36m(setup pid=3476)[0m Unpacking libsensors-config (1:3.6.0-7ubuntu1) ...
[36m(setup pid=3476)[0m Selecting previously unselected package libsensors5:amd64.
[36m(setup pid=3476)[0m Preparing to unpack .../29-libsensors5_1%3a3.6.0-7ubuntu1_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking libsensors5:amd64 (1:3.6.0-7ubuntu1) ...
[36m(setup pid=3476)[0m Preparing to unpack .../30-net-tools_1.60+git20181103.0eebece-1ubuntu5.4_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking net-tools (1.60+git20181103.0eebece-1ubuntu5.4) over (1.60+git20181103.0eebece-1ubuntu5) ...
[36m(setup pid=3476)[0m Selecting previously unselected package sysstat.
[36m(setup pid=3476)[0m Preparing to unpack .../31-sysstat_12.5.2-2ubuntu0.2_amd64.deb ...
[36m(setup pid=3476)[0m Unpacking sysstat (12.5.2-2ubuntu0.2) ...
[36m(setup pid=3476)[0m Setting up libip4tc2:amd64 (1.8.7-1ubuntu5.2) ...
[36m(setup pid=3476)[0m Setting up net-tools (1.60+git20181103.0eebece-1ubuntu5.4) ...
[36m(setup pid=3476)[0m Setting up libapparmor1:amd64 (3.0.4-2ubuntu2.4) ...
[36m(setup pid=3476)[0m Setting up xdg-user-dirs (0.17-2ubuntu4) ...
[36m(setup pid=3476)[0m Setting up libglib2.0-0:amd64 (2.72.4-0ubuntu2.5) ...
[36m(setup pid=3476)[0m No schema files found: doing nothing.
[36m(setup pid=3476)[0m Setting up libargon2-1:amd64 (0~20171227-0.3) ...
[36m(setup pid=3476)[0m Setting up libsensors-config (1:3.6.0-7ubuntu1) ...
[36m(setup pid=3476)[0m Setting up libatm1:amd64 (1:2.5.1-4build2) ...
[36m(setup pid=3476)[0m Setting up libglib2.0-data (2.72.4-0ubuntu2.5) ...
[36m(setup pid=3476)[0m Setting up libdbus-1-3:amd64 (1.12.20-2ubuntu4.1) ...
[36m(setup pid=3476)[0m Setting up dbus (1.12.20-2ubuntu4.1) ...
[36m(setup pid=3476)[0m Setting up shared-mime-info (2.1-2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libmnl0:amd64 (1.0.4-3build2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libsensors5:amd64 (1:3.6.0-7ubuntu1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libxtables12:amd64 (1.8.7-1ubuntu5.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libdevmapper1.02.1:amd64 (2:1.02.175-2.1ubuntu5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up dmsetup (2:1.02.175-2.1ubuntu5) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libnl-genl-3-200:amd64 (3.5.0-0.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libgirepository-1.0-1:amd64 (1.72.0-1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libelf1:amd64 (0.186-1ubuntu0.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libjson-c5:amd64 (0.15-3~ubuntu1.22.04.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up sysstat (12.5.2-2ubuntu0.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m Creating config file /etc/default/sysstat with new version
[36m(setup pid=2560, ip=10.102.30.168)[0m update-alternatives: using /usr/bin/sar.sysstat to provide /usr/bin/sar (sar) in auto mode
[36m(setup pid=2560, ip=10.102.30.168)[0m update-alternatives: warning: skip creation of /usr/share/man/man1/sar.1.gz because associated file /usr/share/man/man1/sar.sysstat.1.gz (of link group sar) doesn't exist
[36m(setup pid=2560, ip=10.102.30.168)[0m Created symlink /etc/systemd/system/sysstat.service.wants/sysstat-collect.timer → /lib/systemd/system/sysstat-collect.timer.
[36m(setup pid=2560, ip=10.102.30.168)[0m Created symlink /etc/systemd/system/sysstat.service.wants/sysstat-summary.timer → /lib/systemd/system/sysstat-summary.timer.
[36m(setup pid=2560, ip=10.102.30.168)[0m Created symlink /etc/systemd/system/multi-user.target.wants/sysstat.service → /lib/systemd/system/sysstat.service.
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up python3-dbus (1.2.18-3build1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up htop (3.0.5-7build2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up gir1.2-glib-2.0:amd64 (1.72.0-1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libcryptsetup12:amd64 (2:2.4.3-1ubuntu1.3) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up libbpf0:amd64 (1:0.5.0-1ubuntu22.04.1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up iproute2 (5.15.0-1ubuntu2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up systemd (249.11-0ubuntu3.16) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m Configuration file '/etc/systemd/system.conf'
[36m(setup pid=2560, ip=10.102.30.168)[0m  ==> File on system created by you or by a script.
[36m(setup pid=2560, ip=10.102.30.168)[0m  ==> File also in package provided by package maintainer.
[36m(setup pid=2560, ip=10.102.30.168)[0m    What would you like to do about it ?  Your options are:
[36m(setup pid=2560, ip=10.102.30.168)[0m     Y or I  : install the package maintainer's version
[36m(setup pid=2560, ip=10.102.30.168)[0m     N or O  : keep your currently-installed version
[36m(setup pid=2560, ip=10.102.30.168)[0m       D     : show the differences between the versions
[36m(setup pid=2560, ip=10.102.30.168)[0m       Z     : start a shell to examine the situation
[36m(setup pid=2560, ip=10.102.30.168)[0m  The default action is to keep your current version.
[36m(setup pid=2560, ip=10.102.30.168)[0m *** system.conf (Y/I/N/O/D/Z) [default=N] ? dpkg: error processing package systemd (--configure):
[36m(setup pid=2560, ip=10.102.30.168)[0m  end of file on stdin at conffile prompt
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up python3-gi (3.42.1-0ubuntu1) ...
[36m(setup pid=3476)[0m Setting up libmnl0:amd64 (1.0.4-3build2) ...
[36m(setup pid=3476)[0m Setting up libsensors5:amd64 (1:3.6.0-7ubuntu1) ...
[36m(setup pid=3476)[0m Setting up libxtables12:amd64 (1.8.7-1ubuntu5.2) ...
[36m(setup pid=3476)[0m Setting up libdevmapper1.02.1:amd64 (2:1.02.175-2.1ubuntu5) ...
[36m(setup pid=3476)[0m Setting up dmsetup (2:1.02.175-2.1ubuntu5) ...
[36m(setup pid=3476)[0m Setting up libnl-genl-3-200:amd64 (3.5.0-0.1) ...
[36m(setup pid=3476)[0m Setting up libgirepository-1.0-1:amd64 (1.72.0-1) ...
[36m(setup pid=3476)[0m Setting up libelf1:amd64 (0.186-1ubuntu0.1) ...
[36m(setup pid=3476)[0m Setting up libjson-c5:amd64 (0.15-3~ubuntu1.22.04.2) ...
[36m(setup pid=3476)[0m Setting up sysstat (12.5.2-2ubuntu0.2) ...
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m Creating config file /etc/default/sysstat with new version
[36m(setup pid=3476)[0m update-alternatives: using /usr/bin/sar.sysstat to provide /usr/bin/sar (sar) in auto mode
[36m(setup pid=3476)[0m update-alternatives: warning: skip creation of /usr/share/man/man1/sar.1.gz because associated file /usr/share/man/man1/sar.sysstat.1.gz (of link group sar) doesn't exist
[36m(setup pid=2560, ip=10.102.30.168)[0m dpkg: dependency problems prevent configuration of systemd-timesyncd:
[36m(setup pid=2560, ip=10.102.30.168)[0m  systemd-timesyncd depends on systemd (= 249.11-0ubuntu3.16); however:
[36m(setup pid=2560, ip=10.102.30.168)[0m   Package systemd is not configured yet.
[36m(setup pid=2560, ip=10.102.30.168)[0m 
[36m(setup pid=2560, ip=10.102.30.168)[0m dpkg: error processing package systemd-timesyncd (--configure):
[36m(setup pid=2560, ip=10.102.30.168)[0m  dependency problems - leaving unconfigured
[36m(setup pid=2560, ip=10.102.30.168)[0m Setting up networkd-dispatcher (2.1-2ubuntu0.22.04.2) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Created symlink /etc/systemd/system/multi-user.target.wants/networkd-dispatcher.service → /lib/systemd/system/networkd-dispatcher.service.
[36m(setup pid=2560, ip=10.102.30.168)[0m Processing triggers for libc-bin (2.35-0ubuntu3.6) ...
[36m(setup pid=3476)[0m Created symlink /etc/systemd/system/sysstat.service.wants/sysstat-collect.timer → /lib/systemd/system/sysstat-collect.timer.
[36m(setup pid=2560, ip=10.102.30.168)[0m Errors were encountered while processing:
[36m(setup pid=2560, ip=10.102.30.168)[0m  systemd
[36m(setup pid=2560, ip=10.102.30.168)[0m  systemd-timesyncd
[36m(setup pid=2560, ip=10.102.30.168)[0m E: Sub-process /usr/bin/dpkg returned an error code (1)
[36m(setup pid=2560, ip=10.102.30.168)[0m Using Python 3.10.12 environment at: /root/training
[36m(setup pid=3476)[0m Created symlink /etc/systemd/system/sysstat.service.wants/sysstat-summary.timer → /lib/systemd/system/sysstat-summary.timer.
[36m(setup pid=3476)[0m Created symlink /etc/systemd/system/multi-user.target.wants/sysstat.service → /lib/systemd/system/sysstat.service.
[36m(setup pid=3476)[0m Setting up python3-dbus (1.2.18-3build1) ...
[36m(setup pid=2560, ip=10.102.30.168)[0m Resolved 3 packages in 144ms
[36m(setup pid=2560, ip=10.102.30.168)[0m Prepared 1 package in 10ms
[36m(setup pid=2560, ip=10.102.30.168)[0m Installed 2 packages in 16ms
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvidia-ml-py==12.575.51
[36m(setup pid=2560, ip=10.102.30.168)[0m  + nvitop==1.5.2
[36m(setup pid=3476)[0m Setting up htop (3.0.5-7build2) ...
[36m(setup pid=3476)[0m Setting up gir1.2-glib-2.0:amd64 (1.72.0-1) ...
[36m(setup pid=3476)[0m Setting up libcryptsetup12:amd64 (2:2.4.3-1ubuntu1.3) ...
[36m(setup pid=3476)[0m Setting up libbpf0:amd64 (1:0.5.0-1ubuntu22.04.1) ...
[36m(setup pid=3476)[0m Setting up iproute2 (5.15.0-1ubuntu2) ...
[36m(setup pid=3476)[0m Setting up systemd (249.11-0ubuntu3.16) ...
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m Configuration file '/etc/systemd/system.conf'
[36m(setup pid=3476)[0m  ==> File on system created by you or by a script.
[36m(setup pid=3476)[0m  ==> File also in package provided by package maintainer.
[36m(setup pid=3476)[0m    What would you like to do about it ?  Your options are:
[36m(setup pid=3476)[0m     Y or I  : install the package maintainer's version
[36m(setup pid=3476)[0m     N or O  : keep your currently-installed version
[36m(setup pid=3476)[0m       D     : show the differences between the versions
[36m(setup pid=3476)[0m       Z     : start a shell to examine the situation
[36m(setup pid=3476)[0m  The default action is to keep your current version.
[36m(setup pid=3476)[0m *** system.conf (Y/I/N/O/D/Z) [default=N] ? dpkg: error processing package systemd (--configure):
[36m(setup pid=3476)[0m  end of file on stdin at conffile prompt
[36m(setup pid=3476)[0m Setting up python3-gi (3.42.1-0ubuntu1) ...
[36m(setup pid=3476)[0m dpkg: dependency problems prevent configuration of systemd-timesyncd:
[36m(setup pid=3476)[0m  systemd-timesyncd depends on systemd (= 249.11-0ubuntu3.16); however:
[36m(setup pid=3476)[0m   Package systemd is not configured yet.
[36m(setup pid=3476)[0m 
[36m(setup pid=3476)[0m dpkg: error processing package systemd-timesyncd (--configure):
[36m(setup pid=3476)[0m  dependency problems - leaving unconfigured
[36m(setup pid=3476)[0m Setting up networkd-dispatcher (2.1-2ubuntu0.22.04.2) ...
[36m(setup pid=3476)[0m Created symlink /etc/systemd/system/multi-user.target.wants/networkd-dispatcher.service → /lib/systemd/system/networkd-dispatcher.service.
[36m(setup pid=3476)[0m Processing triggers for libc-bin (2.35-0ubuntu3.6) ...
[36m(setup pid=3476)[0m Errors were encountered while processing:
[36m(setup pid=3476)[0m  systemd
[36m(setup pid=3476)[0m  systemd-timesyncd
[36m(setup pid=3476)[0m E: Sub-process /usr/bin/dpkg returned an error code (1)
[36m(setup pid=3476)[0m Using Python 3.10.12 environment at: /root/training
[36m(setup pid=3476)[0m Resolved 3 packages in 43ms
[36m(setup pid=3476)[0m Prepared 1 package in 10ms
[36m(setup pid=3476)[0m Installed 2 packages in 17ms
[36m(setup pid=3476)[0m  + nvidia-ml-py==12.575.51
[36m(setup pid=3476)[0m  + nvitop==1.5.2
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Processing directory 1/2: /tmp/checkpoint ===
[36m(head, rank=0, pid=3476)[0m Preserving existing cache directories...
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /tmp/checkpoint/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /tmp/checkpoint/model_cache
[36m(head, rank=0, pid=3476)[0m Downloading and caching dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Processing directory 1/2: /tmp/checkpoint ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Preserving existing cache directories...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /tmp/checkpoint/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /tmp/checkpoint/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Downloading and caching dataset...
[36m(head, rank=0, pid=3476)[0m Setting num_proc from 128 to 10 for the train split as it only contains 10 shards.
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Generating train split:   0%|          | 0/47780 [00:00<?, ? examples/s]
[36m(head, rank=0, pid=3476)[0m Generating train split:   2%|▏         | 1000/47780 [00:00<00:25, 1814.88 examples/s]
[36m(head, rank=0, pid=3476)[0m Generating train split:  19%|█▉        | 9000/47780 [00:00<00:02, 17452.24 examples/s]
[36m(head, rank=0, pid=3476)[0m Generating train split:  29%|██▉       | 14000/47780 [00:00<00:01, 21705.46 examples/s]
[36m(head, rank=0, pid=3476)[0m Generating train split:  44%|████▍     | 21000/47780 [00:00<00:00, 28858.84 examples/s]
[36m(head, rank=0, pid=3476)[0m Generating train split:  61%|██████    | 29000/47780 [00:01<00:00, 37030.54 examples/s]
[36m(head, rank=0, pid=3476)[0m Generating train split:  79%|███████▊  | 37556/47780 [00:01<00:00, 46868.36 examples/s]
[36m(head, rank=0, pid=3476)[0m Generating train split:  97%|█████████▋| 46224/47780 [00:01<00:00, 55992.37 examples/s]
Generating train split: 100%|██████████| 47780/47780 [00:01<00:00, 33389.57 examples/s]
[36m(head, rank=0, pid=3476)[0m Dataset downloaded successfully. Size: 47780 examples
[36m(head, rank=0, pid=3476)[0m Flushing filesystem cache for: /tmp/checkpoint/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Setting num_proc from 128 to 10 for the train split as it only contains 10 shards.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Generating train split:   0%|          | 0/47780 [00:00<?, ? examples/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Generating train split:   2%|▏         | 1000/47780 [00:00<00:24, 1909.01 examples/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Generating train split:  15%|█▍        | 7000/47780 [00:00<00:02, 14213.96 examples/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Generating train split:  23%|██▎       | 11000/47780 [00:00<00:01, 19560.51 examples/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Generating train split:  38%|███▊      | 18000/47780 [00:00<00:00, 31443.52 examples/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Generating train split:  48%|████▊     | 23000/47780 [00:00<00:00, 33261.86 examples/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Generating train split:  63%|██████▎   | 30000/47780 [00:01<00:00, 39845.68 examples/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Generating train split:  88%|████████▊ | 42112/47780 [00:01<00:00, 59941.59 examples/s]
Generating train split: 100%|██████████| 47780/47780 [00:01<00:00, 33803.09 examples/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset downloaded successfully. Size: 47780 examples
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Flushing filesystem cache for: /tmp/checkpoint/dataset_cache
[36m(head, rank=0, pid=3476)[0m Successfully flushed filesystem cache
[36m(head, rank=0, pid=3476)[0m vmtouch output: Files: 14
[36m(head, rank=0, pid=3476)[0m      Directories: 5
[36m(head, rank=0, pid=3476)[0m    Evicted Pages: 1212952 (4G)
[36m(head, rank=0, pid=3476)[0m          Elapsed: 3.7417 seconds
[36m(head, rank=0, pid=3476)[0m Downloading and caching model...
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Successfully flushed filesystem cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m vmtouch output: Files: 14
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m      Directories: 5
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m    Evicted Pages: 1212952 (4G)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m          Elapsed: 3.7494 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Downloading and caching model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(head, rank=0, pid=3476)[0m Fetching 5 files:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(head, rank=0, pid=3476)[0m Fetching 5 files:  20%|██        | 1/5 [00:22<01:29, 22.36s/it]
[36m(head, rank=0, pid=3476)[0m Fetching 5 files:  60%|██████    | 3/5 [00:22<00:11,  5.95s/it]
Fetching 5 files:  80%|████████  | 4/5 [00:24<00:04,  4.37s/it]
Fetching 5 files: 100%|██████████| 5/5 [00:24<00:00,  4.83s/it]
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Fetching 5 files:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Fetching 5 files:  20%|██        | 1/5 [00:27<01:50, 27.73s/it]
Fetching 5 files:  80%|████████  | 4/5 [00:28<00:05,  5.51s/it]
Fetching 5 files: 100%|██████████| 5/5 [00:28<00:00,  5.74s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:04<00:18,  4.64s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:08<00:13,  4.41s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:04<00:17,  4.29s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:13<00:08,  4.34s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:08<00:12,  4.17s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:17<00:04,  4.28s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:21<00:00,  4.16s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:21<00:00,  4.26s/it]
[36m(head, rank=0, pid=3476)[0m Model downloaded and cached at: /tmp/checkpoint/model_cache
[36m(head, rank=0, pid=3476)[0m Flushing filesystem cache for: /tmp/checkpoint/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:12<00:08,  4.17s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:16<00:04,  4.12s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:20<00:00,  4.01s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:20<00:00,  4.08s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model downloaded and cached at: /tmp/checkpoint/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Flushing filesystem cache for: /tmp/checkpoint/model_cache
[36m(head, rank=0, pid=3476)[0m Successfully flushed filesystem cache
[36m(head, rank=0, pid=3476)[0m vmtouch output: Files: 18
[36m(head, rank=0, pid=3476)[0m      Directories: 10
[36m(head, rank=0, pid=3476)[0m    Evicted Pages: 5950909 (22G)
[36m(head, rank=0, pid=3476)[0m          Elapsed: 13.051 seconds
[36m(head, rank=0, pid=3476)[0m Completed processing directory 1/2
[36m(head, rank=0, pid=3476)[0m Flushing filesystem cache for: /tmp/checkpoint/dataset_cache, /tmp/checkpoint/model_cache
[36m(head, rank=0, pid=3476)[0m Successfully flushed filesystem cache
[36m(head, rank=0, pid=3476)[0m vmtouch output: Files: 32
[36m(head, rank=0, pid=3476)[0m      Directories: 15
[36m(head, rank=0, pid=3476)[0m    Evicted Pages: 7163861 (27G)
[36m(head, rank=0, pid=3476)[0m          Elapsed: 0.001457 seconds
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Processing directory 2/2: /mnt/data ===
[36m(head, rank=0, pid=3476)[0m Preserving existing cache directories...
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Downloading and caching dataset...
[36m(head, rank=0, pid=3476)[0m Dataset downloaded successfully. Size: 47780 examples
[36m(head, rank=0, pid=3476)[0m Flushing filesystem cache for: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Successfully flushed filesystem cache
[36m(head, rank=0, pid=3476)[0m vmtouch output: Files: 78
[36m(head, rank=0, pid=3476)[0m      Directories: 5
[36m(head, rank=0, pid=3476)[0m    Evicted Pages: 4617819 (17G)
[36m(head, rank=0, pid=3476)[0m          Elapsed: 0.2751 seconds
[36m(head, rank=0, pid=3476)[0m Downloading and caching model...
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Successfully flushed filesystem cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m vmtouch output: Files: 18
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m      Directories: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m    Evicted Pages: 5950909 (22G)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m          Elapsed: 14.813 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed processing directory 1/2
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Flushing filesystem cache for: /tmp/checkpoint/dataset_cache, /tmp/checkpoint/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Successfully flushed filesystem cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m vmtouch output: Files: 32
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m      Directories: 15
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m    Evicted Pages: 7163861 (27G)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m          Elapsed: 0.001394 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Processing directory 2/2: /mnt/data ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Preserving existing cache directories...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Downloading and caching dataset...
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset downloaded successfully. Size: 47780 examples
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Flushing filesystem cache for: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Successfully flushed filesystem cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m vmtouch output: Files: 78
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m      Directories: 5
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m    Evicted Pages: 4617819 (17G)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m          Elapsed: 0.32897 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Downloading and caching model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:05<00:22,  5.68s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:10<00:15,  5.25s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:07<00:28,  7.03s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:15<00:10,  5.16s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:12<00:18,  6.21s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:21<00:05,  5.41s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:26<00:00,  5.34s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:26<00:00,  5.34s/it]
[36m(head, rank=0, pid=3476)[0m Model downloaded and cached at: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Flushing filesystem cache for: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Successfully flushed filesystem cache
[36m(head, rank=0, pid=3476)[0m vmtouch output: Files: 18
[36m(head, rank=0, pid=3476)[0m      Directories: 10
[36m(head, rank=0, pid=3476)[0m    Evicted Pages: 5950909 (22G)
[36m(head, rank=0, pid=3476)[0m          Elapsed: 1.2621 seconds
[36m(head, rank=0, pid=3476)[0m Completed processing directory 2/2
[36m(head, rank=0, pid=3476)[0m Flushing filesystem cache for: /mnt/data/dataset_cache, /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Successfully flushed filesystem cache
[36m(head, rank=0, pid=3476)[0m vmtouch output: Files: 96
[36m(head, rank=0, pid=3476)[0m      Directories: 15
[36m(head, rank=0, pid=3476)[0m    Evicted Pages: 10568728 (40G)
[36m(head, rank=0, pid=3476)[0m          Elapsed: 0.11152 seconds
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Copying cached data to S3 directories ===
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Copying to S3 directory 1/2: /checkpoints_s3
[36m(head, rank=0, pid=3476)[0m Copying dataset cache from /tmp/checkpoint/dataset_cache to /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:17<00:11,  5.78s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:23<00:05,  5.56s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:27<00:00,  5.27s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:27<00:00,  5.58s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model downloaded and cached at: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Flushing filesystem cache for: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Successfully flushed filesystem cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m vmtouch output: Files: 18
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m      Directories: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m    Evicted Pages: 5950909 (22G)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m          Elapsed: 1.2289 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed processing directory 2/2
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Flushing filesystem cache for: /mnt/data/dataset_cache, /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Successfully flushed filesystem cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m vmtouch output: Files: 96
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m      Directories: 15
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m    Evicted Pages: 10568728 (40G)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m          Elapsed: 0.11599 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Copying cached data to S3 directories ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Copying to S3 directory 1/2: /checkpoints_s3
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Copying dataset cache from /tmp/checkpoint/dataset_cache to /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Dataset cache copied successfully
[36m(head, rank=0, pid=3476)[0m Copying model cache from /tmp/checkpoint/model_cache to /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache copied successfully
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Copying model cache from /tmp/checkpoint/model_cache to /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Model cache copied successfully
[36m(head, rank=0, pid=3476)[0m Copying checkpoints from /tmp/checkpoint/checkpoints to /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m Checkpoints copied successfully
[36m(head, rank=0, pid=3476)[0m Completed copying to S3 directory 1/2
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Copying to S3 directory 2/2: /checkpoints_s3_mount_cached
[36m(head, rank=0, pid=3476)[0m Copying dataset cache from /tmp/checkpoint/dataset_cache to /checkpoints_s3_mount_cached/dataset_cache
[36m(head, rank=0, pid=3476)[0m Dataset cache copied successfully
[36m(head, rank=0, pid=3476)[0m Copying model cache from /tmp/checkpoint/model_cache to /checkpoints_s3_mount_cached/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache copied successfully
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Copying checkpoints from /tmp/checkpoint/checkpoints to /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoints copied successfully
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed copying to S3 directory 1/2
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Copying to S3 directory 2/2: /checkpoints_s3_mount_cached
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Copying dataset cache from /tmp/checkpoint/dataset_cache to /checkpoints_s3_mount_cached/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache copied successfully
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Copying model cache from /tmp/checkpoint/model_cache to /checkpoints_s3_mount_cached/model_cache
[36m(head, rank=0, pid=3476)[0m Model cache copied successfully
[36m(head, rank=0, pid=3476)[0m Copying checkpoints from /tmp/checkpoint/checkpoints to /checkpoints_s3_mount_cached/checkpoints
[36m(head, rank=0, pid=3476)[0m Checkpoints copied successfully
[36m(head, rank=0, pid=3476)[0m Completed copying to S3 directory 2/2
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Download and caching completed ===
[36m(head, rank=0, pid=3476)[0m ipex flag is deprecated, will be removed in Accelerate v1.10. From 2.7.0, PyTorch has all needed optimizations for Intel CPU and XPU.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache copied successfully
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Copying checkpoints from /tmp/checkpoint/checkpoints to /checkpoints_s3_mount_cached/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoints copied successfully
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed copying to S3 directory 2/2
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Download and caching completed ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ipex flag is deprecated, will be removed in Accelerate v1.10. From 2.7.0, PyTorch has all needed optimizations for Intel CPU and XPU.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /mnt/data/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /mnt/data/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /mnt/data/checkpoints
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: 'pytorch_model-00001-of-00012.bin'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: PosixPath('/mnt/data/checkpoints')
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/mnt/data/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.25 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.22 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.26 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.27 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.28 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.30 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.31 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.33 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.29 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.49 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.06 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.10 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.12 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.12 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.15 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.16 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /mnt/data/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 63.90it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 56.07it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 58.56it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 71.70it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 68.71it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 60.55it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 63.42it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.03 seconds
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 64.05it/s]
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.10 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.16 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.16 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.15 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.19 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.05 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.21 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 63.97it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 59.15it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 56.27it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 60.37it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 65.80it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]Completed Load model in 1.06 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 65.04it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.06 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.08 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.14 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.19 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.24 seconds
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:05<00:21,  5.49s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:05<00:23,  5.85s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:10<00:16,  5.33s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:10<00:16,  5.44s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:15<00:10,  5.25s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:16<00:10,  5.36s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:20<00:05,  5.18s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:25<00:00,  4.96s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:25<00:00,  5.10s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 26.49 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:21<00:05,  5.32s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:26<00:00,  5.13s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:26<00:00,  5.26s/it]
[36m(head, rank=0, pid=3476)[0m Completed Load model in 27.39 seconds
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:57,506] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m df: /root/.triton/autotune: No such file or directory
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:58,690] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:58,694] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:58,703] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:58,704] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:58,725] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:58,783] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:58,783] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:16:59,417] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:16:59,417] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:16:59,417] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:16:59,418] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:16:59,418] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:16:59,418] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:16:59,419] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:16:59,419] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m df: /root/.triton/autotune: No such file or directory
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m df: /root/.triton/autotune: No such file or directory
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:59,855] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:59,934] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:59,975] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:59,982] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:16:59,983] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:17:00,044] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:17:00,070] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:17:00,109] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:17:01,986] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:17:01,986] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:17:01,986] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:17:01,986] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:17:01,987] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:17:01,989] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:17:01,989] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:17:01,990] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m   0%|          | 0/10 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_6.json
[36m(head, rank=0, pid=3476)[0m  10%|█         | 1/10 [00:46<06:54, 46.08s/it]Chrome trace exported to: /tmp/trace_10_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_2.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_7.json
[36m(head, rank=0, pid=3476)[0m  20%|██        | 2/10 [01:30<06:02, 45.26s/it]Chrome trace exported to: /tmp/trace_18_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_7.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_4.json
[36m(head, rank=0, pid=3476)[0m  30%|███       | 3/10 [02:15<05:14, 44.89s/it]Chrome trace exported to: /tmp/trace_26_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_7.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_0.json
[36m(head, rank=0, pid=3476)[0m  40%|████      | 4/10 [03:00<04:29, 44.94s/it]Chrome trace exported to: /tmp/trace_34_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_7.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m  50%|█████     | 5/10 [03:46<03:47, 45.40s/it]Chrome trace exported to: /tmp/trace_42_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_7.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_5.json
[36m(head, rank=0, pid=3476)[0m  60%|██████    | 6/10 [04:30<02:59, 44.92s/it]Chrome trace exported to: /tmp/trace_50_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_5.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_7.json
[36m(head, rank=0, pid=3476)[0m  70%|███████   | 7/10 [05:16<02:15, 45.26s/it]Chrome trace exported to: /tmp/trace_58_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_7.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_3.json
[36m(head, rank=0, pid=3476)[0m  80%|████████  | 8/10 [06:02<01:30, 45.47s/it]Chrome trace exported to: /tmp/trace_66_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_6.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_7.json
[36m(head, rank=0, pid=3476)[0m  90%|█████████ | 9/10 [06:47<00:45, 45.51s/it]Chrome trace exported to: /tmp/trace_74_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m 
100%|██████████| 10/10 [07:31<00:00, 44.87s/it]Starting Save checkpoint...Starting Save checkpoint...Starting Save checkpoint...Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m 
                                               
{'loss': 124.4492, 'grad_norm': 35.656002044677734, 'learning_rate': 2.0000000000000003e-06, 'num_tokens': 9501248.0, 'epoch': 0.03}
[36m(head, rank=0, pid=3476)[0m 
100%|██████████| 10/10 [07:31<00:00, 44.87s/it]Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 356.95 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 356.95s (Total: 356.95s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 356.95 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 356.95s (Total: 356.95s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 356.95 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 356.95s (Total: 356.95s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 356.96 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 356.96 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 356.96 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 356.96 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 356.96 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 356.95 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 356.95s (Total: 356.95s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 356.95 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 356.95s (Total: 356.95s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 356.95 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 356.95s (Total: 356.95s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 356.96 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 356.96 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 356.96 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 356.96 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 356.96s (Total: 356.96s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 662.20 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 662.20s (Total: 662.20s)
[36m(head, rank=0, pid=3476)[0m 
                                               
{'train_runtime': 1113.5886, 'train_samples_per_second': 1.149, 'train_steps_per_second': 0.009, 'train_loss': 124.44921875, 'epoch': 0.03}
[36m(head, rank=0, pid=3476)[0m 
100%|██████████| 10/10 [18:33<00:00, 44.87s/it]
100%|██████████| 10/10 [18:33<00:00, 111.36s/it]
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_0.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1143.25 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 662.20s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 662.20s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 662.20s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 662.20s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 0.48s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.05s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.09s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 365.69s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.57s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.87s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 8.14s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /mnt/data/training_run_0_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_0.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.25s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.25s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.25s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.25s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 27.39s
[36m(head, rank=0, pid=3476)[0m   • Min time: 27.39s
[36m(head, rank=0, pid=3476)[0m   • Max time: 27.39s
[36m(head, rank=0, pid=3476)[0m   • Total time: 27.39s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1143.25s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1143.25s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1143.25s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1143.25s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.57s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.87s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 8.14s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 365.69s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.05s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.09s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 0.48s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 662.20s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 662.20s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 662.20s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 662.20s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1172.89s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1172.89s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1172.89s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1172.89s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_0.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m JSON OUTPUT FROM MAIN PROCESS
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m --- Training Run 1 Info (Directory: /mnt/data/) ---
[36m(head, rank=0, pid=3476)[0m {
[36m(head, rank=0, pid=3476)[0m   "run_id": 1,
[36m(head, rank=0, pid=3476)[0m   "timestamp": "2025-08-03T17:16:24.167430",
[36m(head, rank=0, pid=3476)[0m   "dataset_name": "open-r1/codeforces-cots",
[36m(head, rank=0, pid=3476)[0m   "checkpoint_dir": "/mnt/data",
[36m(head, rank=0, pid=3476)[0m   "model_id": "google/gemma-3-12b-it",
[36m(head, rank=0, pid=3476)[0m   "training_status": "completed",
[36m(head, rank=0, pid=3476)[0m   "output_dir": "/mnt/data",
[36m(head, rank=0, pid=3476)[0m   "dataset_load_time": 2.251378059387207,
[36m(head, rank=0, pid=3476)[0m   "model_load_time": 27.38745641708374,
[36m(head, rank=0, pid=3476)[0m   "training_time": 1143.248067855835,
[36m(head, rank=0, pid=3476)[0m   "total_time": 1172.886902332306,
[36m(head, rank=0, pid=3476)[0m   "error": null,
[36m(head, rank=0, pid=3476)[0m   "num_checkpoints_saved": 1,
[36m(head, rank=0, pid=3476)[0m   "total_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m   "average_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m   "min_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m   "max_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m   "individual_checkpoint_save_times": [
[36m(head, rank=0, pid=3476)[0m     662.2019898891449
[36m(head, rank=0, pid=3476)[0m   ],
[36m(head, rank=0, pid=3476)[0m   "num_batch_samples": 10,
[36m(head, rank=0, pid=3476)[0m   "total_batch_sample_time": 0.4821741580963135,
[36m(head, rank=0, pid=3476)[0m   "average_batch_sample_time": 0.04821741580963135,
[36m(head, rank=0, pid=3476)[0m   "min_batch_sample_time": 0.03333878517150879,
[36m(head, rank=0, pid=3476)[0m   "max_batch_sample_time": 0.08567023277282715,
[36m(head, rank=0, pid=3476)[0m   "individual_batch_sample_times": [
[36m(head, rank=0, pid=3476)[0m     0.08567023277282715,
[36m(head, rank=0, pid=3476)[0m     0.05173230171203613,
[36m(head, rank=0, pid=3476)[0m     0.07031822204589844,
[36m(head, rank=0, pid=3476)[0m     0.04412436485290527,
[36m(head, rank=0, pid=3476)[0m     0.0485076904296875,
[36m(head, rank=0, pid=3476)[0m     0.037309885025024414,
[36m(head, rank=0, pid=3476)[0m     0.039301395416259766,
[36m(head, rank=0, pid=3476)[0m     0.03837251663208008,
[36m(head, rank=0, pid=3476)[0m     0.03349876403808594,
[36m(head, rank=0, pid=3476)[0m     0.03333878517150879
[36m(head, rank=0, pid=3476)[0m   ],
[36m(head, rank=0, pid=3476)[0m   "num_training_steps": 80,
[36m(head, rank=0, pid=3476)[0m   "total_training_step_time": 365.69113969802856,
[36m(head, rank=0, pid=3476)[0m   "average_training_step_time": 4.571139246225357,
[36m(head, rank=0, pid=3476)[0m   "min_training_step_time": 3.871112585067749,
[36m(head, rank=0, pid=3476)[0m   "max_training_step_time": 8.140053510665894,
[36m(head, rank=0, pid=3476)[0m   "individual_training_step_times": [
[36m(head, rank=0, pid=3476)[0m     8.140053510665894,
[36m(head, rank=0, pid=3476)[0m     4.791445732116699,
[36m(head, rank=0, pid=3476)[0m     5.255297660827637,
[36m(head, rank=0, pid=3476)[0m     4.216633558273315,
[36m(head, rank=0, pid=3476)[0m     5.187345504760742,
[36m(head, rank=0, pid=3476)[0m     4.010840654373169,
[36m(head, rank=0, pid=3476)[0m     4.163445234298706,
[36m(head, rank=0, pid=3476)[0m     4.186964511871338,
[36m(head, rank=0, pid=3476)[0m     4.194631099700928,
[36m(head, rank=0, pid=3476)[0m     5.256656646728516,
[36m(head, rank=0, pid=3476)[0m     4.282236099243164,
[36m(head, rank=0, pid=3476)[0m     4.154593229293823,
[36m(head, rank=0, pid=3476)[0m     4.092456340789795,
[36m(head, rank=0, pid=3476)[0m     5.478837251663208,
[36m(head, rank=0, pid=3476)[0m     4.253929376602173,
[36m(head, rank=0, pid=3476)[0m     4.76608943939209,
[36m(head, rank=0, pid=3476)[0m     5.01261043548584,
[36m(head, rank=0, pid=3476)[0m     4.416217803955078,
[36m(head, rank=0, pid=3476)[0m     4.124937534332275,
[36m(head, rank=0, pid=3476)[0m     4.146371841430664,
[36m(head, rank=0, pid=3476)[0m     4.7799084186553955,
[36m(head, rank=0, pid=3476)[0m     4.172359466552734,
[36m(head, rank=0, pid=3476)[0m     5.328099489212036,
[36m(head, rank=0, pid=3476)[0m     4.246214151382446,
[36m(head, rank=0, pid=3476)[0m     5.470584869384766,
[36m(head, rank=0, pid=3476)[0m     4.228365659713745,
[36m(head, rank=0, pid=3476)[0m     4.561403036117554,
[36m(head, rank=0, pid=3476)[0m     4.205440282821655,
[36m(head, rank=0, pid=3476)[0m     4.1081976890563965,
[36m(head, rank=0, pid=3476)[0m     4.1768999099731445,
[36m(head, rank=0, pid=3476)[0m     4.820796728134155,
[36m(head, rank=0, pid=3476)[0m     4.8430516719818115,
[36m(head, rank=0, pid=3476)[0m     5.321784019470215,
[36m(head, rank=0, pid=3476)[0m     4.393391847610474,
[36m(head, rank=0, pid=3476)[0m     4.921175479888916,
[36m(head, rank=0, pid=3476)[0m     4.578927278518677,
[36m(head, rank=0, pid=3476)[0m     4.766574859619141,
[36m(head, rank=0, pid=3476)[0m     4.654609203338623,
[36m(head, rank=0, pid=3476)[0m     4.306933403015137,
[36m(head, rank=0, pid=3476)[0m     4.214005947113037,
[36m(head, rank=0, pid=3476)[0m     3.972693920135498,
[36m(head, rank=0, pid=3476)[0m     3.994170665740967,
[36m(head, rank=0, pid=3476)[0m     5.9226789474487305,
[36m(head, rank=0, pid=3476)[0m     4.188553333282471,
[36m(head, rank=0, pid=3476)[0m     4.423117637634277,
[36m(head, rank=0, pid=3476)[0m     4.132116794586182,
[36m(head, rank=0, pid=3476)[0m     4.384525299072266,
[36m(head, rank=0, pid=3476)[0m     4.294096946716309,
[36m(head, rank=0, pid=3476)[0m     5.204485893249512,
[36m(head, rank=0, pid=3476)[0m     5.155587673187256,
[36m(head, rank=0, pid=3476)[0m     4.382388114929199,
[36m(head, rank=0, pid=3476)[0m     4.160944223403931,
[36m(head, rank=0, pid=3476)[0m     4.404864311218262,
[36m(head, rank=0, pid=3476)[0m     4.6859166622161865,
[36m(head, rank=0, pid=3476)[0m     4.885669946670532,
[36m(head, rank=0, pid=3476)[0m     3.96846079826355,
[36m(head, rank=0, pid=3476)[0m     5.4567742347717285,
[36m(head, rank=0, pid=3476)[0m     4.5215370655059814,
[36m(head, rank=0, pid=3476)[0m     4.560173511505127,
[36m(head, rank=0, pid=3476)[0m     4.7955241203308105,
[36m(head, rank=0, pid=3476)[0m     4.166051387786865,
[36m(head, rank=0, pid=3476)[0m     4.169478178024292,
[36m(head, rank=0, pid=3476)[0m     4.108154058456421,
[36m(head, rank=0, pid=3476)[0m     4.865273952484131,
[36m(head, rank=0, pid=3476)[0m     4.011037826538086,
[36m(head, rank=0, pid=3476)[0m     3.9923596382141113,
[36m(head, rank=0, pid=3476)[0m     5.115009307861328,
[36m(head, rank=0, pid=3476)[0m     3.9361798763275146,
[36m(head, rank=0, pid=3476)[0m     4.61083722114563,
[36m(head, rank=0, pid=3476)[0m     5.62255859375,
[36m(head, rank=0, pid=3476)[0m     4.47942042350769,
[36m(head, rank=0, pid=3476)[0m     4.573275566101074,
[36m(head, rank=0, pid=3476)[0m     3.907029867172241,
[36m(head, rank=0, pid=3476)[0m     4.741662263870239,
[36m(head, rank=0, pid=3476)[0m     4.693761825561523,
[36m(head, rank=0, pid=3476)[0m     4.249265432357788,
[36m(head, rank=0, pid=3476)[0m     4.441148519515991,
[36m(head, rank=0, pid=3476)[0m     4.1940789222717285,
[36m(head, rank=0, pid=3476)[0m     3.871112585067749,
[36m(head, rank=0, pid=3476)[0m     4.218847274780273
[36m(head, rank=0, pid=3476)[0m   ]
[36m(head, rank=0, pid=3476)[0m }
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m --- All Training Runs Summary ---
[36m(head, rank=0, pid=3476)[0m [
[36m(head, rank=0, pid=3476)[0m   {
[36m(head, rank=0, pid=3476)[0m     "run_id": 1,
[36m(head, rank=0, pid=3476)[0m     "timestamp": "2025-08-03T17:16:24.167430",
[36m(head, rank=0, pid=3476)[0m     "dataset_name": "open-r1/codeforces-cots",
[36m(head, rank=0, pid=3476)[0m     "checkpoint_dir": "/mnt/data",
[36m(head, rank=0, pid=3476)[0m     "model_id": "google/gemma-3-12b-it",
[36m(head, rank=0, pid=3476)[0m     "training_status": "completed",
[36m(head, rank=0, pid=3476)[0m     "output_dir": "/mnt/data",
[36m(head, rank=0, pid=3476)[0m     "dataset_load_time": 2.251378059387207,
[36m(head, rank=0, pid=3476)[0m     "model_load_time": 27.38745641708374,
[36m(head, rank=0, pid=3476)[0m     "training_time": 1143.248067855835,
[36m(head, rank=0, pid=3476)[0m     "total_time": 1172.886902332306,
[36m(head, rank=0, pid=3476)[0m     "error": null,
[36m(head, rank=0, pid=3476)[0m     "num_checkpoints_saved": 1,
[36m(head, rank=0, pid=3476)[0m     "total_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m     "average_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m     "min_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m     "max_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m     "individual_checkpoint_save_times": [
[36m(head, rank=0, pid=3476)[0m       662.2019898891449
[36m(head, rank=0, pid=3476)[0m     ],
[36m(head, rank=0, pid=3476)[0m     "num_batch_samples": 10,
[36m(head, rank=0, pid=3476)[0m     "total_batch_sample_time": 0.4821741580963135,
[36m(head, rank=0, pid=3476)[0m     "average_batch_sample_time": 0.04821741580963135,
[36m(head, rank=0, pid=3476)[0m     "min_batch_sample_time": 0.03333878517150879,
[36m(head, rank=0, pid=3476)[0m     "max_batch_sample_time": 0.08567023277282715,
[36m(head, rank=0, pid=3476)[0m     "individual_batch_sample_times": [
[36m(head, rank=0, pid=3476)[0m       0.08567023277282715,
[36m(head, rank=0, pid=3476)[0m       0.05173230171203613,
[36m(head, rank=0, pid=3476)[0m       0.07031822204589844,
[36m(head, rank=0, pid=3476)[0m       0.04412436485290527,
[36m(head, rank=0, pid=3476)[0m       0.0485076904296875,
[36m(head, rank=0, pid=3476)[0m       0.037309885025024414,
[36m(head, rank=0, pid=3476)[0m       0.039301395416259766,
[36m(head, rank=0, pid=3476)[0m       0.03837251663208008,
[36m(head, rank=0, pid=3476)[0m       0.03349876403808594,
[36m(head, rank=0, pid=3476)[0m       0.03333878517150879
[36m(head, rank=0, pid=3476)[0m     ],
[36m(head, rank=0, pid=3476)[0m     "num_training_steps": 80,
[36m(head, rank=0, pid=3476)[0m     "total_training_step_time": 365.69113969802856,
[36m(head, rank=0, pid=3476)[0m     "average_training_step_time": 4.571139246225357,
[36m(head, rank=0, pid=3476)[0m     "min_training_step_time": 3.871112585067749,
[36m(head, rank=0, pid=3476)[0m     "max_training_step_time": 8.140053510665894,
[36m(head, rank=0, pid=3476)[0m     "individual_training_step_times": [
[36m(head, rank=0, pid=3476)[0m       8.140053510665894,
[36m(head, rank=0, pid=3476)[0m       4.791445732116699,
[36m(head, rank=0, pid=3476)[0m       5.255297660827637,
[36m(head, rank=0, pid=3476)[0m       4.216633558273315,
[36m(head, rank=0, pid=3476)[0m       5.187345504760742,
[36m(head, rank=0, pid=3476)[0m       4.010840654373169,
[36m(head, rank=0, pid=3476)[0m       4.163445234298706,
[36m(head, rank=0, pid=3476)[0m       4.186964511871338,
[36m(head, rank=0, pid=3476)[0m       4.194631099700928,
[36m(head, rank=0, pid=3476)[0m       5.256656646728516,
[36m(head, rank=0, pid=3476)[0m       4.282236099243164,
[36m(head, rank=0, pid=3476)[0m       4.154593229293823,
[36m(head, rank=0, pid=3476)[0m       4.092456340789795,
[36m(head, rank=0, pid=3476)[0m       5.478837251663208,
[36m(head, rank=0, pid=3476)[0m       4.253929376602173,
[36m(head, rank=0, pid=3476)[0m       4.76608943939209,
[36m(head, rank=0, pid=3476)[0m       5.01261043548584,
[36m(head, rank=0, pid=3476)[0m       4.416217803955078,
[36m(head, rank=0, pid=3476)[0m       4.124937534332275,
[36m(head, rank=0, pid=3476)[0m       4.146371841430664,
[36m(head, rank=0, pid=3476)[0m       4.7799084186553955,
[36m(head, rank=0, pid=3476)[0m       4.172359466552734,
[36m(head, rank=0, pid=3476)[0m       5.328099489212036,
[36m(head, rank=0, pid=3476)[0m       4.246214151382446,
[36m(head, rank=0, pid=3476)[0m       5.470584869384766,
[36m(head, rank=0, pid=3476)[0m       4.228365659713745,
[36m(head, rank=0, pid=3476)[0m       4.561403036117554,
[36m(head, rank=0, pid=3476)[0m       4.205440282821655,
[36m(head, rank=0, pid=3476)[0m       4.1081976890563965,
[36m(head, rank=0, pid=3476)[0m       4.1768999099731445,
[36m(head, rank=0, pid=3476)[0m       4.820796728134155,
[36m(head, rank=0, pid=3476)[0m       4.8430516719818115,
[36m(head, rank=0, pid=3476)[0m       5.321784019470215,
[36m(head, rank=0, pid=3476)[0m       4.393391847610474,
[36m(head, rank=0, pid=3476)[0m       4.921175479888916,
[36m(head, rank=0, pid=3476)[0m       4.578927278518677,
[36m(head, rank=0, pid=3476)[0m       4.766574859619141,
[36m(head, rank=0, pid=3476)[0m       4.654609203338623,
[36m(head, rank=0, pid=3476)[0m       4.306933403015137,
[36m(head, rank=0, pid=3476)[0m       4.214005947113037,
[36m(head, rank=0, pid=3476)[0m       3.972693920135498,
[36m(head, rank=0, pid=3476)[0m       3.994170665740967,
[36m(head, rank=0, pid=3476)[0m       5.9226789474487305,
[36m(head, rank=0, pid=3476)[0m       4.188553333282471,
[36m(head, rank=0, pid=3476)[0m       4.423117637634277,
[36m(head, rank=0, pid=3476)[0m       4.132116794586182,
[36m(head, rank=0, pid=3476)[0m       4.384525299072266,
[36m(head, rank=0, pid=3476)[0m       4.294096946716309,
[36m(head, rank=0, pid=3476)[0m       5.204485893249512,
[36m(head, rank=0, pid=3476)[0m       5.155587673187256,
[36m(head, rank=0, pid=3476)[0m       4.382388114929199,
[36m(head, rank=0, pid=3476)[0m       4.160944223403931,
[36m(head, rank=0, pid=3476)[0m       4.404864311218262,
[36m(head, rank=0, pid=3476)[0m       4.6859166622161865,
[36m(head, rank=0, pid=3476)[0m       4.885669946670532,
[36m(head, rank=0, pid=3476)[0m       3.96846079826355,
[36m(head, rank=0, pid=3476)[0m       5.4567742347717285,
[36m(head, rank=0, pid=3476)[0m       4.5215370655059814,
[36m(head, rank=0, pid=3476)[0m       4.560173511505127,
[36m(head, rank=0, pid=3476)[0m       4.7955241203308105,
[36m(head, rank=0, pid=3476)[0m       4.166051387786865,
[36m(head, rank=0, pid=3476)[0m       4.169478178024292,
[36m(head, rank=0, pid=3476)[0m       4.108154058456421,
[36m(head, rank=0, pid=3476)[0m       4.865273952484131,
[36m(head, rank=0, pid=3476)[0m       4.011037826538086,
[36m(head, rank=0, pid=3476)[0m       3.9923596382141113,
[36m(head, rank=0, pid=3476)[0m       5.115009307861328,
[36m(head, rank=0, pid=3476)[0m       3.9361798763275146,
[36m(head, rank=0, pid=3476)[0m       4.61083722114563,
[36m(head, rank=0, pid=3476)[0m       5.62255859375,
[36m(head, rank=0, pid=3476)[0m       4.47942042350769,
[36m(head, rank=0, pid=3476)[0m       4.573275566101074,
[36m(head, rank=0, pid=3476)[0m       3.907029867172241,
[36m(head, rank=0, pid=3476)[0m       4.741662263870239,
[36m(head, rank=0, pid=3476)[0m       4.693761825561523,
[36m(head, rank=0, pid=3476)[0m       4.249265432357788,
[36m(head, rank=0, pid=3476)[0m       4.441148519515991,
[36m(head, rank=0, pid=3476)[0m       4.1940789222717285,
[36m(head, rank=0, pid=3476)[0m       3.871112585067749,
[36m(head, rank=0, pid=3476)[0m       4.218847274780273
[36m(head, rank=0, pid=3476)[0m     ]
[36m(head, rank=0, pid=3476)[0m   }
[36m(head, rank=0, pid=3476)[0m ]
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m --- Timing Summary ---
[36m(head, rank=0, pid=3476)[0m {
[36m(head, rank=0, pid=3476)[0m   "total_runs": 1,
[36m(head, rank=0, pid=3476)[0m   "dataset_loading": {
[36m(head, rank=0, pid=3476)[0m     "dataset_load_count": 1,
[36m(head, rank=0, pid=3476)[0m     "dataset_load_average": 2.251378059387207,
[36m(head, rank=0, pid=3476)[0m     "dataset_load_min": 2.251378059387207,
[36m(head, rank=0, pid=3476)[0m     "dataset_load_max": 2.251378059387207,
[36m(head, rank=0, pid=3476)[0m     "dataset_load_total": 2.251378059387207
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "model_loading": {
[36m(head, rank=0, pid=3476)[0m     "model_load_count": 1,
[36m(head, rank=0, pid=3476)[0m     "model_load_average": 27.38745641708374,
[36m(head, rank=0, pid=3476)[0m     "model_load_min": 27.38745641708374,
[36m(head, rank=0, pid=3476)[0m     "model_load_max": 27.38745641708374,
[36m(head, rank=0, pid=3476)[0m     "model_load_total": 27.38745641708374
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "training": {
[36m(head, rank=0, pid=3476)[0m     "training_count": 1,
[36m(head, rank=0, pid=3476)[0m     "training_average": 1143.248067855835,
[36m(head, rank=0, pid=3476)[0m     "training_min": 1143.248067855835,
[36m(head, rank=0, pid=3476)[0m     "training_max": 1143.248067855835,
[36m(head, rank=0, pid=3476)[0m     "training_total": 1143.248067855835
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "total_run_time": {
[36m(head, rank=0, pid=3476)[0m     "total_count": 1,
[36m(head, rank=0, pid=3476)[0m     "total_average": 1172.886902332306,
[36m(head, rank=0, pid=3476)[0m     "total_min": 1172.886902332306,
[36m(head, rank=0, pid=3476)[0m     "total_max": 1172.886902332306,
[36m(head, rank=0, pid=3476)[0m     "total_total": 1172.886902332306
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "checkpoint_saving": {
[36m(head, rank=0, pid=3476)[0m     "total_checkpoints_saved": 1,
[36m(head, rank=0, pid=3476)[0m     "total_checkpoint_save_time": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m     "average_save_time_per_checkpoint": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m     "checkpoint_save_times_count": 1,
[36m(head, rank=0, pid=3476)[0m     "checkpoint_save_min": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m     "checkpoint_save_max": 662.2019898891449,
[36m(head, rank=0, pid=3476)[0m     "checkpoint_save_average": 662.2019898891449
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "batch_sampling": {
[36m(head, rank=0, pid=3476)[0m     "total_batch_samples": 10,
[36m(head, rank=0, pid=3476)[0m     "total_batch_sample_time": 0.4821741580963135,
[36m(head, rank=0, pid=3476)[0m     "average_sample_time_per_batch": 0.04821741580963135,
[36m(head, rank=0, pid=3476)[0m     "batch_sample_times_count": 10,
[36m(head, rank=0, pid=3476)[0m     "batch_sample_min": 0.03333878517150879,
[36m(head, rank=0, pid=3476)[0m     "batch_sample_max": 0.08567023277282715,
[36m(head, rank=0, pid=3476)[0m     "batch_sample_average": 0.04821741580963135
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "training_steps": {
[36m(head, rank=0, pid=3476)[0m     "total_training_steps": 80,
[36m(head, rank=0, pid=3476)[0m     "total_training_step_time": 365.69113969802856,
[36m(head, rank=0, pid=3476)[0m     "average_step_time_per_step": 4.571139246225357,
[36m(head, rank=0, pid=3476)[0m     "training_step_times_count": 80,
[36m(head, rank=0, pid=3476)[0m     "training_step_min": 3.871112585067749,
[36m(head, rank=0, pid=3476)[0m     "training_step_max": 8.140053510665894,
[36m(head, rank=0, pid=3476)[0m     "training_step_average": 4.571139246225357
[36m(head, rank=0, pid=3476)[0m   }
[36m(head, rank=0, pid=3476)[0m }
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1214.27 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 0.53s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 0.16s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 370.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.63s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.84s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 9.54s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /mnt/data/training_run_2_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.16s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.16s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.16s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.16s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1214.27s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1214.27s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1214.27s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1214.27s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.63s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.84s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 9.54s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 370.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 0.16s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 0.53s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1217.48s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1217.48s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1217.48s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1217.48s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1214.42 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 0.46s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 0.13s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 369.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.81s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 9.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /mnt/data/training_run_1_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1214.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1214.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1214.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1214.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.81s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 9.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 369.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 0.13s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 0.46s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1217.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1217.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1217.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1217.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1190.64 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 0.48s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 0.09s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 369.18s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.61s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.88s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 8.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /mnt/data/training_run_0_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.22s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.22s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.22s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.22s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 26.49s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 26.49s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 26.49s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 26.49s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1190.64s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1190.64s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1190.64s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1190.64s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.61s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.88s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 8.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 369.18s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 0.09s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 0.48s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1219.35s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1219.35s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1219.35s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1219.35s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m JSON OUTPUT FROM MAIN PROCESS
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m --- Training Run 1 Info (Directory: /mnt/data/) ---
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "run_id": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "timestamp": "2025-08-03T17:16:24.166840",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "dataset_name": "open-r1/codeforces-cots",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "checkpoint_dir": "/mnt/data",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "model_id": "google/gemma-3-12b-it",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "training_status": "completed",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "output_dir": "/mnt/data",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "dataset_load_time": 2.218167781829834,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "model_load_time": 26.491857051849365,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "training_time": 1190.642718553543,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_time": 1219.3527433872223,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "error": null,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "num_checkpoints_saved": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "average_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "min_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "max_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "individual_checkpoint_save_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     356.9540650844574
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   ],
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "num_batch_samples": 10,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_batch_sample_time": 0.4839022159576416,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "average_batch_sample_time": 0.04839022159576416,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "min_batch_sample_time": 0.028623580932617188,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "max_batch_sample_time": 0.09157323837280273,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "individual_batch_sample_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.09157323837280273,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.0542902946472168,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.05554604530334473,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.058371543884277344,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.04857754707336426,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.03591799736022949,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.03605246543884277,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.03372335433959961,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.04122614860534668,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.028623580932617188
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   ],
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "num_training_steps": 80,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_training_step_time": 369.17742347717285,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "average_training_step_time": 4.614717793464661,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "min_training_step_time": 3.884112596511841,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "max_training_step_time": 8.432772397994995,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "individual_training_step_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     8.432772397994995,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.412920236587524,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.256107330322266,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.217737913131714,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.219071388244629,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.01198148727417,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.544621706008911,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.189064025878906,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.434704780578613,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.229775905609131,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.2297728061676025,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.1543288230896,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.093078136444092,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.481621980667114,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.356847286224365,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.267453670501709,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.928911447525024,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.418138027191162,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.445907115936279,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.143895864486694,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.8014092445373535,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.174692630767822,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.506971836090088,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.249591827392578,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.461235523223877,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.327796220779419,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.687579870223999,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.206417560577393,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9571659564971924,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.176397800445557,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.030032396316528,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.842416524887085,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.378721237182617,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.393963098526001,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.064286708831787,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.579430103302002,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.767858505249023,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.613471269607544,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.463397264480591,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.21638560295105,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.884112596511841,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.146739721298218,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.909681558609009,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.187063932418823,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.447340488433838,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.136773586273193,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.560702323913574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.296056747436523,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.209540843963623,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.162673234939575,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.374313592910767,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.158706903457642,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.404431343078613,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.595573425292969,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.092248439788818,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.96264910697937,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.469289779663086,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.521886825561523,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.776819229125977,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.773796319961548,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.018397808074951,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.170886516571045,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.445396900177002,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.765402317047119,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.010022401809692,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9040513038635254,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.363474130630493,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.019619941711426,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.599236726760864,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.622748613357544,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.576122522354126,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.572075605392456,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.054685831069946,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.776804447174072,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.836264133453369,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.244170904159546,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.354423522949219,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.167356252670288,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.022377252578735,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.215572834014893
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   ]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m }
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m --- All Training Runs Summary ---
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "run_id": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "timestamp": "2025-08-03T17:16:24.166840",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_name": "open-r1/codeforces-cots",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_dir": "/mnt/data",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_id": "google/gemma-3-12b-it",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_status": "completed",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "output_dir": "/mnt/data",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_time": 2.218167781829834,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_time": 26.491857051849365,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_time": 1190.642718553543,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_time": 1219.3527433872223,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "error": null,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "num_checkpoints_saved": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "min_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "max_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "individual_checkpoint_save_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       356.9540650844574
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     ],
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "num_batch_samples": 10,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_batch_sample_time": 0.4839022159576416,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_batch_sample_time": 0.04839022159576416,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "min_batch_sample_time": 0.028623580932617188,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "max_batch_sample_time": 0.09157323837280273,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "individual_batch_sample_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.09157323837280273,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.0542902946472168,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.05554604530334473,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.058371543884277344,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.04857754707336426,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.03591799736022949,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.03605246543884277,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.03372335433959961,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.04122614860534668,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.028623580932617188
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     ],
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "num_training_steps": 80,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_training_step_time": 369.17742347717285,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_training_step_time": 4.614717793464661,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "min_training_step_time": 3.884112596511841,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "max_training_step_time": 8.432772397994995,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "individual_training_step_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       8.432772397994995,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.412920236587524,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.256107330322266,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.217737913131714,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.219071388244629,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.01198148727417,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.544621706008911,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.189064025878906,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.434704780578613,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.229775905609131,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.2297728061676025,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.1543288230896,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.093078136444092,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.481621980667114,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.356847286224365,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.267453670501709,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.928911447525024,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.418138027191162,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.445907115936279,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.143895864486694,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.8014092445373535,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.174692630767822,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.506971836090088,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.249591827392578,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.461235523223877,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.327796220779419,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.687579870223999,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.206417560577393,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9571659564971924,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.176397800445557,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.030032396316528,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.842416524887085,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.378721237182617,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.393963098526001,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.064286708831787,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.579430103302002,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.767858505249023,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.613471269607544,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.463397264480591,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.21638560295105,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.884112596511841,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.146739721298218,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.909681558609009,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.187063932418823,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.447340488433838,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.136773586273193,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.560702323913574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.296056747436523,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.209540843963623,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.162673234939575,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.374313592910767,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.158706903457642,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.404431343078613,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.595573425292969,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.092248439788818,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.96264910697937,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.469289779663086,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.521886825561523,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.776819229125977,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.773796319961548,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.018397808074951,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.170886516571045,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.445396900177002,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.765402317047119,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.010022401809692,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9040513038635254,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.363474130630493,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.019619941711426,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.599236726760864,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.622748613357544,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.576122522354126,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.572075605392456,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.054685831069946,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.776804447174072,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.836264133453369,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.244170904159546,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.354423522949219,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.167356252670288,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.022377252578735,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.215572834014893
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     ]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   }
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m --- Timing Summary ---
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_runs": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "dataset_loading": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_average": 2.218167781829834,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_min": 2.218167781829834,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_max": 2.218167781829834,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_total": 2.218167781829834
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "model_loading": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_average": 26.491857051849365,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_min": 26.491857051849365,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_max": 26.491857051849365,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_total": 26.491857051849365
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "training": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_average": 1190.642718553543,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_min": 1190.642718553543,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_max": 1190.642718553543,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_total": 1190.642718553543
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_run_time": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_average": 1219.3527433872223,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_min": 1219.3527433872223,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_max": 1219.3527433872223,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_total": 1219.3527433872223
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "checkpoint_saving": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_checkpoints_saved": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_checkpoint_save_time": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_save_time_per_checkpoint": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_save_times_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_save_min": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_save_max": 356.9540650844574,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_save_average": 356.9540650844574
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "batch_sampling": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_batch_samples": 10,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_batch_sample_time": 0.4839022159576416,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_sample_time_per_batch": 0.04839022159576416,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "batch_sample_times_count": 10,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "batch_sample_min": 0.028623580932617188,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "batch_sample_max": 0.09157323837280273,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "batch_sample_average": 0.04839022159576416
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "training_steps": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_training_steps": 80,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_training_step_time": 369.17742347717285,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_step_time_per_step": 4.614717793464661,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_step_times_count": 80,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_step_min": 3.884112596511841,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_step_max": 8.432772397994995,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_step_average": 4.614717793464661
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   }
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m }
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_7.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1216.02 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 356.96s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 0.53s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.05s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.15s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 362.57s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.53s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.73s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 9.55s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /mnt/data/training_run_7_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1216.66 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 0.55s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 0.16s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 369.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.61s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.84s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 9.54s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /mnt/data/training_run_7_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1215.82 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 0.56s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 0.17s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 369.70s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 9.53s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /mnt/data/training_run_4_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_7.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.49s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.49s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.49s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.49s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.05s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.05s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.05s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.05s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1216.02s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1216.02s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1216.02s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1216.02s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.53s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.73s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 9.55s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 362.57s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.05s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.15s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 0.53s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 356.96s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1219.57s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1219.57s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1219.57s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1219.57s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.29s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.29s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.29s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.29s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1216.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1216.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1216.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1216.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.61s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.84s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 9.54s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 369.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 0.16s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 0.55s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1219.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1219.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1219.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1219.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.19s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.19s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.19s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.19s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1215.82s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1215.82s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1215.82s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1215.82s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 9.53s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 369.70s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 0.17s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 0.56s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 356.95s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1219.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1219.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1219.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1219.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_4.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1216.41 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 356.95s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 0.54s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.05s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.16s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 367.57s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.59s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.86s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 9.54s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /mnt/data/training_run_4_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_3.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1216.50 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 356.96s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 0.53s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.05s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.15s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 367.40s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.59s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.94s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 9.55s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /mnt/data/training_run_3_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_4.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.31s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.31s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.31s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.31s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.15s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.15s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.15s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.15s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1216.41s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1216.41s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1216.41s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1216.41s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.59s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.86s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 9.54s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 367.57s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.05s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.16s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 0.54s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 356.95s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1219.87s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1219.87s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1219.87s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1219.87s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_4.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_3.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.27s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.27s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.27s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.27s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.16s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.16s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.16s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.16s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1216.50s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1216.50s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1216.50s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1216.50s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.59s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.94s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 9.55s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 367.40s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.05s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.15s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 0.53s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 356.96s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1219.93s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1219.93s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1219.93s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1219.93s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1216.45 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 0.56s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 0.17s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 366.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.58s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.92s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 9.55s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /mnt/data/training_run_5_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1216.45s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1216.45s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1216.45s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1216.45s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.58s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.92s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 9.55s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 366.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 0.17s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 0.56s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1219.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1219.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1219.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1219.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1216.65 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 0.59s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 0.18s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 369.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 9.52s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /mnt/data/training_run_3_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.10s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.10s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.10s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.10s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.24s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.24s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.24s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.24s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1216.65s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1216.65s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1216.65s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1216.65s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 9.52s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 369.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.06s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 0.18s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 0.59s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1219.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1219.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1219.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1219.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1216.89 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 0.52s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 0.17s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 367.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.59s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.83s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 9.53s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /mnt/data/training_run_6_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.14s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.14s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.14s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.14s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1216.89s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1216.89s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1216.89s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1216.89s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.59s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.83s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 9.53s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 367.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.05s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 0.17s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 0.52s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 356.96s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1220.14s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1220.14s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1220.14s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1220.14s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_1.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1217.56 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 356.95s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 0.50s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.05s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.15s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 367.00s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.59s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.82s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 9.55s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /mnt/data/training_run_1_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_1.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.28s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.28s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.28s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.28s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.19s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.19s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.19s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.19s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1217.56s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1217.56s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1217.56s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1217.56s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.59s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.82s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 9.55s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 367.00s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.05s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.15s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 0.50s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 356.95s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1221.03s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1221.03s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1221.03s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1221.03s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_5.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1218.02 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 356.96s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 0.59s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.06s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.17s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 364.93s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.56s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.90s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 9.53s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /mnt/data/training_run_5_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_5.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.30s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.30s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.30s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.30s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.10s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.10s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.10s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.10s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1218.02s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1218.02s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1218.02s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1218.02s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.56s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.90s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 9.53s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 364.93s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.06s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.17s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 0.59s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 356.96s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1221.42s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1221.42s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1221.42s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1221.42s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_6.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1218.16 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 356.96s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 0.55s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.05s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.15s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 366.91s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.59s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.91s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 9.55s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /mnt/data/training_run_6_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_6.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.26s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.26s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.26s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.26s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.16s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.16s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.16s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.16s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1218.16s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1218.16s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1218.16s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1218.16s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.59s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.91s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 9.55s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 366.91s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.05s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.15s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 0.55s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 356.96s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 356.96s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1221.58s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1221.58s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1221.58s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1221.58s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_2.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1219.14 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 356.95s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 0.52s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.05s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.16s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 364.45s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.56s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.86s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 9.54s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /mnt/data/training_run_2_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_2.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.33s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.33s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.33s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.33s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.21s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.21s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.21s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.21s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1219.14s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1219.14s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1219.14s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1219.14s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.56s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.86s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 9.54s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 364.45s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.05s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.03s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.16s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 0.52s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 356.95s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 356.95s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1222.69s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1222.69s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1222.69s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1222.69s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m cp: target '/mnt/data/checkpoint/checkpoints' is not a directory
[36m(head, rank=0, pid=3476)[0m cp: target '/mnt/data/checkpoint/checkpoints' is not a directory
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ipex flag is deprecated, will be removed in Accelerate v1.10. From 2.7.0, PyTorch has all needed optimizations for Intel CPU and XPU.
[36m(head, rank=0, pid=3476)[0m ipex flag is deprecated, will be removed in Accelerate v1.10. From 2.7.0, PyTorch has all needed optimizations for Intel CPU and XPU.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m === Starting Run 1/1 ===
[36m(head, rank=0, pid=3476)[0m Dataset: open-r1/codeforces-cots
[36m(head, rank=0, pid=3476)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(head, rank=0, pid=3476)[0m Model cache: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m === Starting Run 1/1 ===
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset: open-r1/codeforces-cots
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset cache: /checkpoints_s3/dataset_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model cache: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint saving dir: /checkpoints_s3/checkpoints
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/checkpoints_s3/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/checkpoints_s3/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/checkpoints_s3/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/checkpoints_s3/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/checkpoints_s3/checkpoints'
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(head, rank=0, pid=3476)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/checkpoints_s3/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/checkpoints_s3/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Removing checkpoint exception [Errno 2] No such file or directory: '/checkpoints_s3/checkpoints'
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load dataset...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.87 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.83 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.92 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.87 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.89 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.90 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.87 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load dataset in 2.88 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Load model...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.91 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.85 secondsStarting Load model...
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.87 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.84 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.97 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.91 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.94 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(head, rank=0, pid=3476)[0m Completed Load dataset in 2.85 seconds
[36m(head, rank=0, pid=3476)[0m Starting Load model...
[36m(head, rank=0, pid=3476)[0m Loading model from cache directory: /checkpoints_s3/model_cache
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:  20%|██        | 1/5 [00:00<00:00,  6.86it/s]
Loading checkpoint shards:  20%|██        | 1/5 [00:00<00:00,  6.75it/s]
Loading checkpoint shards:  20%|██        | 1/5 [00:00<00:00,  8.68it/s]
Loading checkpoint shards:  20%|██        | 1/5 [00:00<00:00,  6.11it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:00<00:00,  9.25it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00,  8.36it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00,  7.17it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00,  7.47it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00,  7.50it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00, 11.07it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:   0%|          | 0/5 [00:00<?, ?it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00, 12.28it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00, 10.69it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00, 14.06it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00, 14.96it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:00<00:00,  4.19it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00,  8.56it/s]
Loading checkpoint shards:  40%|████      | 2/5 [00:00<00:00,  4.98it/s]
Loading checkpoint shards:  60%|██████    | 3/5 [00:00<00:00,  6.03it/s]
Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00,  8.01it/s]
Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00,  9.49it/s]
Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00,  8.16it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:00<00:00, 23.33it/s]
Loading checkpoint shards:  60%|██████    | 3/5 [00:00<00:00,  9.27it/s]
Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00, 10.41it/s]
Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00,  9.80it/s]
Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00,  9.34it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00, 10.53it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00,  8.60it/s]
Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00,  5.90it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.96it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.14it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  8.73it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  8.52it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.22it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  8.01it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  8.03it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.76it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.37it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.45it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.84it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.15it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.33it/s]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:00<00:00,  8.63it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  8.75it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  9.22it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  8.36it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  7.85it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 10.13it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00, 11.38it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  8.39it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  9.96it/s]
[36m(head, rank=0, pid=3476)[0m 
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  8.66it/s]
Loading checkpoint shards: 100%|██████████| 5/5 [00:00<00:00,  9.07it/s]
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.72 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.71 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.71 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.73 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.73 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.73 seconds
[36m(head, rank=0, pid=3476)[0m Completed Load model in 1.73 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.77 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.78 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.76 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.78 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.78 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.78 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 1.79 seconds
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:10<00:41, 10.43s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  20%|██        | 1/5 [00:10<00:40, 10.18s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:19<00:29,  9.81s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  40%|████      | 2/5 [00:20<00:29,  9.98s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:30<00:20, 10.23s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  60%|██████    | 3/5 [00:29<00:19,  9.75s/it]
[36m(head, rank=0, pid=3476)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:39<00:09,  9.81s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:48<00:00,  9.63s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:48<00:00,  9.74s/it]
[36m(head, rank=0, pid=3476)[0m Completed Load model in 49.98 seconds
[36m(head, rank=0, pid=3476)[0m Starting Training...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Loading checkpoint shards:  80%|████████  | 4/5 [00:39<00:09,  9.76s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:49<00:00,  9.76s/it]
Loading checkpoint shards: 100%|██████████| 5/5 [00:49<00:00,  9.87s/it]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Load model in 50.39 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Training...
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:33,892] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:34,848] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:34,851] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:34,859] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:34,861] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:34,861] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:34,863] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:34,880] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:35,099] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:36,092] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:36,105] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:36,128] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:36,130] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:36,130] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:36,139] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m [2025-08-03 17:38:36,141] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m backward_prefetch is not supported in FSDP2. Setting backward prefetch to None.
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:54,572] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:54,586] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:54,591] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:54,594] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:54,640] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:54,653] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:54,661] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:54,665] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:55,657] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:55,805] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:55,892] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:55,898] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:55,899] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:55,955] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:55,965] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [2025-08-03 17:38:56,019] [INFO] [logging.py:107:log_dist] [Rank -1] [TorchCheckpointEngine] Initialized with serialization = False
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m   0%|          | 0/10 [00:00<?, ?it/s]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_7.json
[36m(head, rank=0, pid=3476)[0m  10%|█         | 1/10 [00:40<06:02, 40.30s/it]Chrome trace exported to: /tmp/trace_10_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_10_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_10_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_14_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_14_0.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_1.json
[36m(head, rank=0, pid=3476)[0m  20%|██        | 2/10 [01:22<05:29, 41.16s/it]Chrome trace exported to: /tmp/trace_18_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_18_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_18_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_22_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_22_3.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_2.json
[36m(head, rank=0, pid=3476)[0m  30%|███       | 3/10 [02:03<04:47, 41.13s/it]Chrome trace exported to: /tmp/trace_26_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_26_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_26_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_30_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_30_1.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m  40%|████      | 4/10 [02:44<04:07, 41.29s/it]Chrome trace exported to: /tmp/trace_34_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_34_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_34_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_38_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_38_3.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_3.json
[36m(head, rank=0, pid=3476)[0m  50%|█████     | 5/10 [03:27<03:29, 41.99s/it]Chrome trace exported to: /tmp/trace_42_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_42_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_42_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_46_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_46_0.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_4.json
[36m(head, rank=0, pid=3476)[0m  60%|██████    | 6/10 [04:09<02:47, 41.77s/it]Chrome trace exported to: /tmp/trace_50_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_50_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_50_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_54_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_54_2.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_7.json
[36m(head, rank=0, pid=3476)[0m  70%|███████   | 7/10 [04:52<02:06, 42.24s/it]Chrome trace exported to: /tmp/trace_58_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_58_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_58_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_62_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_62_2.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_6.json
[36m(head, rank=0, pid=3476)[0m  80%|████████  | 8/10 [05:35<01:25, 42.64s/it]Chrome trace exported to: /tmp/trace_66_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_66_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_66_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_70_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_70_6.json
[36m(head, rank=0, pid=3476)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_6.json
[36m(head, rank=0, pid=3476)[0m  90%|█████████ | 9/10 [06:18<00:42, 42.47s/it]Chrome trace exported to: /tmp/trace_74_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_74_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_2.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_74_3.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_0.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_78_1.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_78_2.json
[36m(head, rank=0, pid=3476)[0m 
100%|██████████| 10/10 [06:59<00:00, 42.02s/it]Starting Save checkpoint...Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m 
                                               
{'loss': 124.4492, 'grad_norm': 35.656002044677734, 'learning_rate': 2.0000000000000003e-06, 'num_tokens': 9501248.0, 'epoch': 0.03}
[36m(head, rank=0, pid=3476)[0m 
100%|██████████| 10/10 [06:59<00:00, 42.02s/it]Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Starting Save checkpoint...
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 256.41 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 256.41s (Total: 256.41s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 256.41 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 256.41s (Total: 256.41s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 256.42 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 256.42s (Total: 256.42s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 256.42 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 256.42s (Total: 256.42s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 256.43 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 256.43s (Total: 256.43s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 256.43 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 256.43s (Total: 256.43s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 256.43 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 256.43s (Total: 256.43s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 256.43 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 256.43s (Total: 256.43s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 256.44 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 256.44s (Total: 256.44s)
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Save checkpoint in 256.44 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint save time: 256.44s (Total: 256.44s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 256.42 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 256.42s (Total: 256.42s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 256.42 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 256.42s (Total: 256.42s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 256.42 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 256.42s (Total: 256.42s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 256.42 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 256.42s (Total: 256.42s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 256.43 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 256.43s (Total: 256.43s)
[36m(head, rank=0, pid=3476)[0m Completed Save checkpoint in 443.58 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint save time: 443.58s (Total: 443.58s)
[36m(head, rank=0, pid=3476)[0m 
                                               
{'train_runtime': 862.696, 'train_samples_per_second': 1.484, 'train_steps_per_second': 0.012, 'train_loss': 124.44921875, 'epoch': 0.03}
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m 100%|██████████| 10/10 [14:22<00:00, 42.02s/it]
100%|██████████| 10/10 [14:22<00:00, 86.29s/it]
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_0.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 936.15 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 443.58s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 443.58s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 443.58s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 443.58s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 7.95s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.80s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.64s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 1.02s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 326.39s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.08s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.56s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 5.30s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /checkpoints_s3/training_run_0_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_0.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.97s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.97s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.97s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.97s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 49.98s
[36m(head, rank=0, pid=3476)[0m   • Min time: 49.98s
[36m(head, rank=0, pid=3476)[0m   • Max time: 49.98s
[36m(head, rank=0, pid=3476)[0m   • Total time: 49.98s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 936.15s
[36m(head, rank=0, pid=3476)[0m   • Min time: 936.15s
[36m(head, rank=0, pid=3476)[0m   • Max time: 936.15s
[36m(head, rank=0, pid=3476)[0m   • Total time: 936.15s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.08s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.56s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 5.30s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 326.39s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.80s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.64s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 1.02s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 7.95s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 443.58s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 443.58s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 443.58s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 443.58s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 989.11s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 989.11s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 989.11s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 989.11s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_0.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m JSON OUTPUT FROM MAIN PROCESS
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m --- Training Run 1 Info (Directory: /checkpoints_s3) ---
[36m(head, rank=0, pid=3476)[0m {
[36m(head, rank=0, pid=3476)[0m   "run_id": 1,
[36m(head, rank=0, pid=3476)[0m   "timestamp": "2025-08-03T17:37:18.925061",
[36m(head, rank=0, pid=3476)[0m   "dataset_name": "open-r1/codeforces-cots",
[36m(head, rank=0, pid=3476)[0m   "checkpoint_dir": "/checkpoints_s3",
[36m(head, rank=0, pid=3476)[0m   "model_id": "google/gemma-3-12b-it",
[36m(head, rank=0, pid=3476)[0m   "training_status": "completed",
[36m(head, rank=0, pid=3476)[0m   "output_dir": "/checkpoints_s3",
[36m(head, rank=0, pid=3476)[0m   "dataset_load_time": 2.9687445163726807,
[36m(head, rank=0, pid=3476)[0m   "model_load_time": 49.984182596206665,
[36m(head, rank=0, pid=3476)[0m   "training_time": 936.1527705192566,
[36m(head, rank=0, pid=3476)[0m   "total_time": 989.1056976318359,
[36m(head, rank=0, pid=3476)[0m   "error": null,
[36m(head, rank=0, pid=3476)[0m   "num_checkpoints_saved": 1,
[36m(head, rank=0, pid=3476)[0m   "total_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m   "average_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m   "min_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m   "max_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m   "individual_checkpoint_save_times": [
[36m(head, rank=0, pid=3476)[0m     443.58356857299805
[36m(head, rank=0, pid=3476)[0m   ],
[36m(head, rank=0, pid=3476)[0m   "num_batch_samples": 10,
[36m(head, rank=0, pid=3476)[0m   "total_batch_sample_time": 7.95168924331665,
[36m(head, rank=0, pid=3476)[0m   "average_batch_sample_time": 0.7951689243316651,
[36m(head, rank=0, pid=3476)[0m   "min_batch_sample_time": 0.6429579257965088,
[36m(head, rank=0, pid=3476)[0m   "max_batch_sample_time": 1.0178799629211426,
[36m(head, rank=0, pid=3476)[0m   "individual_batch_sample_times": [
[36m(head, rank=0, pid=3476)[0m     1.0178799629211426,
[36m(head, rank=0, pid=3476)[0m     0.772594690322876,
[36m(head, rank=0, pid=3476)[0m     0.8729174137115479,
[36m(head, rank=0, pid=3476)[0m     0.7782459259033203,
[36m(head, rank=0, pid=3476)[0m     0.7101123332977295,
[36m(head, rank=0, pid=3476)[0m     0.8659243583679199,
[36m(head, rank=0, pid=3476)[0m     0.707526445388794,
[36m(head, rank=0, pid=3476)[0m     0.6429579257965088,
[36m(head, rank=0, pid=3476)[0m     0.736572265625,
[36m(head, rank=0, pid=3476)[0m     0.8469579219818115
[36m(head, rank=0, pid=3476)[0m   ],
[36m(head, rank=0, pid=3476)[0m   "num_training_steps": 80,
[36m(head, rank=0, pid=3476)[0m   "total_training_step_time": 326.39195823669434,
[36m(head, rank=0, pid=3476)[0m   "average_training_step_time": 4.079899477958679,
[36m(head, rank=0, pid=3476)[0m   "min_training_step_time": 3.5622329711914062,
[36m(head, rank=0, pid=3476)[0m   "max_training_step_time": 5.304590463638306,
[36m(head, rank=0, pid=3476)[0m   "individual_training_step_times": [
[36m(head, rank=0, pid=3476)[0m     5.304590463638306,
[36m(head, rank=0, pid=3476)[0m     4.126294851303101,
[36m(head, rank=0, pid=3476)[0m     4.375555992126465,
[36m(head, rank=0, pid=3476)[0m     3.6724631786346436,
[36m(head, rank=0, pid=3476)[0m     4.741941690444946,
[36m(head, rank=0, pid=3476)[0m     3.86466646194458,
[36m(head, rank=0, pid=3476)[0m     3.5622329711914062,
[36m(head, rank=0, pid=3476)[0m     3.7072038650512695,
[36m(head, rank=0, pid=3476)[0m     3.9441044330596924,
[36m(head, rank=0, pid=3476)[0m     4.677318096160889,
[36m(head, rank=0, pid=3476)[0m     3.9325621128082275,
[36m(head, rank=0, pid=3476)[0m     3.7046725749969482,
[36m(head, rank=0, pid=3476)[0m     3.7050552368164062,
[36m(head, rank=0, pid=3476)[0m     4.956719875335693,
[36m(head, rank=0, pid=3476)[0m     3.9205408096313477,
[36m(head, rank=0, pid=3476)[0m     4.091527700424194,
[36m(head, rank=0, pid=3476)[0m     4.140126466751099,
[36m(head, rank=0, pid=3476)[0m     3.733114719390869,
[36m(head, rank=0, pid=3476)[0m     4.0393288135528564,
[36m(head, rank=0, pid=3476)[0m     3.7054855823516846,
[36m(head, rank=0, pid=3476)[0m     4.301140546798706,
[36m(head, rank=0, pid=3476)[0m     3.7046492099761963,
[36m(head, rank=0, pid=3476)[0m     4.793461084365845,
[36m(head, rank=0, pid=3476)[0m     3.735351800918579,
[36m(head, rank=0, pid=3476)[0m     5.130923748016357,
[36m(head, rank=0, pid=3476)[0m     3.608776092529297,
[36m(head, rank=0, pid=3476)[0m     4.06399393081665,
[36m(head, rank=0, pid=3476)[0m     3.726952314376831,
[36m(head, rank=0, pid=3476)[0m     3.724198341369629,
[36m(head, rank=0, pid=3476)[0m     3.711230993270874,
[36m(head, rank=0, pid=3476)[0m     4.047348260879517,
[36m(head, rank=0, pid=3476)[0m     4.370885848999023,
[36m(head, rank=0, pid=3476)[0m     4.904582262039185,
[36m(head, rank=0, pid=3476)[0m     3.8828237056732178,
[36m(head, rank=0, pid=3476)[0m     3.9463274478912354,
[36m(head, rank=0, pid=3476)[0m     4.089860200881958,
[36m(head, rank=0, pid=3476)[0m     4.53670072555542,
[36m(head, rank=0, pid=3476)[0m     4.421767473220825,
[36m(head, rank=0, pid=3476)[0m     4.090197324752808,
[36m(head, rank=0, pid=3476)[0m     3.8711459636688232,
[36m(head, rank=0, pid=3476)[0m     3.7373359203338623,
[36m(head, rank=0, pid=3476)[0m     3.5642151832580566,
[36m(head, rank=0, pid=3476)[0m     5.050010681152344,
[36m(head, rank=0, pid=3476)[0m     3.707413911819458,
[36m(head, rank=0, pid=3476)[0m     3.885651111602783,
[36m(head, rank=0, pid=3476)[0m     3.928863763809204,
[36m(head, rank=0, pid=3476)[0m     4.040395498275757,
[36m(head, rank=0, pid=3476)[0m     3.9057209491729736,
[36m(head, rank=0, pid=3476)[0m     5.028666019439697,
[36m(head, rank=0, pid=3476)[0m     4.815873622894287,
[36m(head, rank=0, pid=3476)[0m     3.8373007774353027,
[36m(head, rank=0, pid=3476)[0m     3.701906681060791,
[36m(head, rank=0, pid=3476)[0m     3.9680662155151367,
[36m(head, rank=0, pid=3476)[0m     3.7129745483398438,
[36m(head, rank=0, pid=3476)[0m     4.547054290771484,
[36m(head, rank=0, pid=3476)[0m     3.7065398693084717,
[36m(head, rank=0, pid=3476)[0m     5.209078311920166,
[36m(head, rank=0, pid=3476)[0m     4.341204881668091,
[36m(head, rank=0, pid=3476)[0m     4.055121898651123,
[36m(head, rank=0, pid=3476)[0m     4.133283615112305,
[36m(head, rank=0, pid=3476)[0m     3.7107510566711426,
[36m(head, rank=0, pid=3476)[0m     3.750143527984619,
[36m(head, rank=0, pid=3476)[0m     4.01958155632019,
[36m(head, rank=0, pid=3476)[0m     4.500384569168091,
[36m(head, rank=0, pid=3476)[0m     3.7431118488311768,
[36m(head, rank=0, pid=3476)[0m     3.718928337097168,
[36m(head, rank=0, pid=3476)[0m     4.018078088760376,
[36m(head, rank=0, pid=3476)[0m     3.586650848388672,
[36m(head, rank=0, pid=3476)[0m     4.270588636398315,
[36m(head, rank=0, pid=3476)[0m     4.8446197509765625,
[36m(head, rank=0, pid=3476)[0m     3.845884323120117,
[36m(head, rank=0, pid=3476)[0m     3.89473032951355,
[36m(head, rank=0, pid=3476)[0m     3.5728211402893066,
[36m(head, rank=0, pid=3476)[0m     4.180004596710205,
[36m(head, rank=0, pid=3476)[0m     3.9572038650512695,
[36m(head, rank=0, pid=3476)[0m     4.063743591308594,
[36m(head, rank=0, pid=3476)[0m     3.952597141265869,
[36m(head, rank=0, pid=3476)[0m     3.73050856590271,
[36m(head, rank=0, pid=3476)[0m     3.8794846534729004,
[36m(head, rank=0, pid=3476)[0m     3.7076408863067627
[36m(head, rank=0, pid=3476)[0m   ]
[36m(head, rank=0, pid=3476)[0m }
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m --- All Training Runs Summary ---
[36m(head, rank=0, pid=3476)[0m [
[36m(head, rank=0, pid=3476)[0m   {
[36m(head, rank=0, pid=3476)[0m     "run_id": 1,
[36m(head, rank=0, pid=3476)[0m     "timestamp": "2025-08-03T17:37:18.925061",
[36m(head, rank=0, pid=3476)[0m     "dataset_name": "open-r1/codeforces-cots",
[36m(head, rank=0, pid=3476)[0m     "checkpoint_dir": "/checkpoints_s3",
[36m(head, rank=0, pid=3476)[0m     "model_id": "google/gemma-3-12b-it",
[36m(head, rank=0, pid=3476)[0m     "training_status": "completed",
[36m(head, rank=0, pid=3476)[0m     "output_dir": "/checkpoints_s3",
[36m(head, rank=0, pid=3476)[0m     "dataset_load_time": 2.9687445163726807,
[36m(head, rank=0, pid=3476)[0m     "model_load_time": 49.984182596206665,
[36m(head, rank=0, pid=3476)[0m     "training_time": 936.1527705192566,
[36m(head, rank=0, pid=3476)[0m     "total_time": 989.1056976318359,
[36m(head, rank=0, pid=3476)[0m     "error": null,
[36m(head, rank=0, pid=3476)[0m     "num_checkpoints_saved": 1,
[36m(head, rank=0, pid=3476)[0m     "total_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m     "average_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m     "min_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m     "max_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m     "individual_checkpoint_save_times": [
[36m(head, rank=0, pid=3476)[0m       443.58356857299805
[36m(head, rank=0, pid=3476)[0m     ],
[36m(head, rank=0, pid=3476)[0m     "num_batch_samples": 10,
[36m(head, rank=0, pid=3476)[0m     "total_batch_sample_time": 7.95168924331665,
[36m(head, rank=0, pid=3476)[0m     "average_batch_sample_time": 0.7951689243316651,
[36m(head, rank=0, pid=3476)[0m     "min_batch_sample_time": 0.6429579257965088,
[36m(head, rank=0, pid=3476)[0m     "max_batch_sample_time": 1.0178799629211426,
[36m(head, rank=0, pid=3476)[0m     "individual_batch_sample_times": [
[36m(head, rank=0, pid=3476)[0m       1.0178799629211426,
[36m(head, rank=0, pid=3476)[0m       0.772594690322876,
[36m(head, rank=0, pid=3476)[0m       0.8729174137115479,
[36m(head, rank=0, pid=3476)[0m       0.7782459259033203,
[36m(head, rank=0, pid=3476)[0m       0.7101123332977295,
[36m(head, rank=0, pid=3476)[0m       0.8659243583679199,
[36m(head, rank=0, pid=3476)[0m       0.707526445388794,
[36m(head, rank=0, pid=3476)[0m       0.6429579257965088,
[36m(head, rank=0, pid=3476)[0m       0.736572265625,
[36m(head, rank=0, pid=3476)[0m       0.8469579219818115
[36m(head, rank=0, pid=3476)[0m     ],
[36m(head, rank=0, pid=3476)[0m     "num_training_steps": 80,
[36m(head, rank=0, pid=3476)[0m     "total_training_step_time": 326.39195823669434,
[36m(head, rank=0, pid=3476)[0m     "average_training_step_time": 4.079899477958679,
[36m(head, rank=0, pid=3476)[0m     "min_training_step_time": 3.5622329711914062,
[36m(head, rank=0, pid=3476)[0m     "max_training_step_time": 5.304590463638306,
[36m(head, rank=0, pid=3476)[0m     "individual_training_step_times": [
[36m(head, rank=0, pid=3476)[0m       5.304590463638306,
[36m(head, rank=0, pid=3476)[0m       4.126294851303101,
[36m(head, rank=0, pid=3476)[0m       4.375555992126465,
[36m(head, rank=0, pid=3476)[0m       3.6724631786346436,
[36m(head, rank=0, pid=3476)[0m       4.741941690444946,
[36m(head, rank=0, pid=3476)[0m       3.86466646194458,
[36m(head, rank=0, pid=3476)[0m       3.5622329711914062,
[36m(head, rank=0, pid=3476)[0m       3.7072038650512695,
[36m(head, rank=0, pid=3476)[0m       3.9441044330596924,
[36m(head, rank=0, pid=3476)[0m       4.677318096160889,
[36m(head, rank=0, pid=3476)[0m       3.9325621128082275,
[36m(head, rank=0, pid=3476)[0m       3.7046725749969482,
[36m(head, rank=0, pid=3476)[0m       3.7050552368164062,
[36m(head, rank=0, pid=3476)[0m       4.956719875335693,
[36m(head, rank=0, pid=3476)[0m       3.9205408096313477,
[36m(head, rank=0, pid=3476)[0m       4.091527700424194,
[36m(head, rank=0, pid=3476)[0m       4.140126466751099,
[36m(head, rank=0, pid=3476)[0m       3.733114719390869,
[36m(head, rank=0, pid=3476)[0m       4.0393288135528564,
[36m(head, rank=0, pid=3476)[0m       3.7054855823516846,
[36m(head, rank=0, pid=3476)[0m       4.301140546798706,
[36m(head, rank=0, pid=3476)[0m       3.7046492099761963,
[36m(head, rank=0, pid=3476)[0m       4.793461084365845,
[36m(head, rank=0, pid=3476)[0m       3.735351800918579,
[36m(head, rank=0, pid=3476)[0m       5.130923748016357,
[36m(head, rank=0, pid=3476)[0m       3.608776092529297,
[36m(head, rank=0, pid=3476)[0m       4.06399393081665,
[36m(head, rank=0, pid=3476)[0m       3.726952314376831,
[36m(head, rank=0, pid=3476)[0m       3.724198341369629,
[36m(head, rank=0, pid=3476)[0m       3.711230993270874,
[36m(head, rank=0, pid=3476)[0m       4.047348260879517,
[36m(head, rank=0, pid=3476)[0m       4.370885848999023,
[36m(head, rank=0, pid=3476)[0m       4.904582262039185,
[36m(head, rank=0, pid=3476)[0m       3.8828237056732178,
[36m(head, rank=0, pid=3476)[0m       3.9463274478912354,
[36m(head, rank=0, pid=3476)[0m       4.089860200881958,
[36m(head, rank=0, pid=3476)[0m       4.53670072555542,
[36m(head, rank=0, pid=3476)[0m       4.421767473220825,
[36m(head, rank=0, pid=3476)[0m       4.090197324752808,
[36m(head, rank=0, pid=3476)[0m       3.8711459636688232,
[36m(head, rank=0, pid=3476)[0m       3.7373359203338623,
[36m(head, rank=0, pid=3476)[0m       3.5642151832580566,
[36m(head, rank=0, pid=3476)[0m       5.050010681152344,
[36m(head, rank=0, pid=3476)[0m       3.707413911819458,
[36m(head, rank=0, pid=3476)[0m       3.885651111602783,
[36m(head, rank=0, pid=3476)[0m       3.928863763809204,
[36m(head, rank=0, pid=3476)[0m       4.040395498275757,
[36m(head, rank=0, pid=3476)[0m       3.9057209491729736,
[36m(head, rank=0, pid=3476)[0m       5.028666019439697,
[36m(head, rank=0, pid=3476)[0m       4.815873622894287,
[36m(head, rank=0, pid=3476)[0m       3.8373007774353027,
[36m(head, rank=0, pid=3476)[0m       3.701906681060791,
[36m(head, rank=0, pid=3476)[0m       3.9680662155151367,
[36m(head, rank=0, pid=3476)[0m       3.7129745483398438,
[36m(head, rank=0, pid=3476)[0m       4.547054290771484,
[36m(head, rank=0, pid=3476)[0m       3.7065398693084717,
[36m(head, rank=0, pid=3476)[0m       5.209078311920166,
[36m(head, rank=0, pid=3476)[0m       4.341204881668091,
[36m(head, rank=0, pid=3476)[0m       4.055121898651123,
[36m(head, rank=0, pid=3476)[0m       4.133283615112305,
[36m(head, rank=0, pid=3476)[0m       3.7107510566711426,
[36m(head, rank=0, pid=3476)[0m       3.750143527984619,
[36m(head, rank=0, pid=3476)[0m       4.01958155632019,
[36m(head, rank=0, pid=3476)[0m       4.500384569168091,
[36m(head, rank=0, pid=3476)[0m       3.7431118488311768,
[36m(head, rank=0, pid=3476)[0m       3.718928337097168,
[36m(head, rank=0, pid=3476)[0m       4.018078088760376,
[36m(head, rank=0, pid=3476)[0m       3.586650848388672,
[36m(head, rank=0, pid=3476)[0m       4.270588636398315,
[36m(head, rank=0, pid=3476)[0m       4.8446197509765625,
[36m(head, rank=0, pid=3476)[0m       3.845884323120117,
[36m(head, rank=0, pid=3476)[0m       3.89473032951355,
[36m(head, rank=0, pid=3476)[0m       3.5728211402893066,
[36m(head, rank=0, pid=3476)[0m       4.180004596710205,
[36m(head, rank=0, pid=3476)[0m       3.9572038650512695,
[36m(head, rank=0, pid=3476)[0m       4.063743591308594,
[36m(head, rank=0, pid=3476)[0m       3.952597141265869,
[36m(head, rank=0, pid=3476)[0m       3.73050856590271,
[36m(head, rank=0, pid=3476)[0m       3.8794846534729004,
[36m(head, rank=0, pid=3476)[0m       3.7076408863067627
[36m(head, rank=0, pid=3476)[0m     ]
[36m(head, rank=0, pid=3476)[0m   }
[36m(head, rank=0, pid=3476)[0m ]
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m --- Timing Summary ---
[36m(head, rank=0, pid=3476)[0m {
[36m(head, rank=0, pid=3476)[0m   "total_runs": 1,
[36m(head, rank=0, pid=3476)[0m   "dataset_loading": {
[36m(head, rank=0, pid=3476)[0m     "dataset_load_count": 1,
[36m(head, rank=0, pid=3476)[0m     "dataset_load_average": 2.9687445163726807,
[36m(head, rank=0, pid=3476)[0m     "dataset_load_min": 2.9687445163726807,
[36m(head, rank=0, pid=3476)[0m     "dataset_load_max": 2.9687445163726807,
[36m(head, rank=0, pid=3476)[0m     "dataset_load_total": 2.9687445163726807
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "model_loading": {
[36m(head, rank=0, pid=3476)[0m     "model_load_count": 1,
[36m(head, rank=0, pid=3476)[0m     "model_load_average": 49.984182596206665,
[36m(head, rank=0, pid=3476)[0m     "model_load_min": 49.984182596206665,
[36m(head, rank=0, pid=3476)[0m     "model_load_max": 49.984182596206665,
[36m(head, rank=0, pid=3476)[0m     "model_load_total": 49.984182596206665
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "training": {
[36m(head, rank=0, pid=3476)[0m     "training_count": 1,
[36m(head, rank=0, pid=3476)[0m     "training_average": 936.1527705192566,
[36m(head, rank=0, pid=3476)[0m     "training_min": 936.1527705192566,
[36m(head, rank=0, pid=3476)[0m     "training_max": 936.1527705192566,
[36m(head, rank=0, pid=3476)[0m     "training_total": 936.1527705192566
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "total_run_time": {
[36m(head, rank=0, pid=3476)[0m     "total_count": 1,
[36m(head, rank=0, pid=3476)[0m     "total_average": 989.1056976318359,
[36m(head, rank=0, pid=3476)[0m     "total_min": 989.1056976318359,
[36m(head, rank=0, pid=3476)[0m     "total_max": 989.1056976318359,
[36m(head, rank=0, pid=3476)[0m     "total_total": 989.1056976318359
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "checkpoint_saving": {
[36m(head, rank=0, pid=3476)[0m     "total_checkpoints_saved": 1,
[36m(head, rank=0, pid=3476)[0m     "total_checkpoint_save_time": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m     "average_save_time_per_checkpoint": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m     "checkpoint_save_times_count": 1,
[36m(head, rank=0, pid=3476)[0m     "checkpoint_save_min": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m     "checkpoint_save_max": 443.58356857299805,
[36m(head, rank=0, pid=3476)[0m     "checkpoint_save_average": 443.58356857299805
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "batch_sampling": {
[36m(head, rank=0, pid=3476)[0m     "total_batch_samples": 10,
[36m(head, rank=0, pid=3476)[0m     "total_batch_sample_time": 7.95168924331665,
[36m(head, rank=0, pid=3476)[0m     "average_sample_time_per_batch": 0.7951689243316651,
[36m(head, rank=0, pid=3476)[0m     "batch_sample_times_count": 10,
[36m(head, rank=0, pid=3476)[0m     "batch_sample_min": 0.6429579257965088,
[36m(head, rank=0, pid=3476)[0m     "batch_sample_max": 1.0178799629211426,
[36m(head, rank=0, pid=3476)[0m     "batch_sample_average": 0.7951689243316651
[36m(head, rank=0, pid=3476)[0m   },
[36m(head, rank=0, pid=3476)[0m   "training_steps": {
[36m(head, rank=0, pid=3476)[0m     "total_training_steps": 80,
[36m(head, rank=0, pid=3476)[0m     "total_training_step_time": 326.39195823669434,
[36m(head, rank=0, pid=3476)[0m     "average_step_time_per_step": 4.079899477958679,
[36m(head, rank=0, pid=3476)[0m     "training_step_times_count": 80,
[36m(head, rank=0, pid=3476)[0m     "training_step_min": 3.5622329711914062,
[36m(head, rank=0, pid=3476)[0m     "training_step_max": 5.304590463638306,
[36m(head, rank=0, pid=3476)[0m     "training_step_average": 4.079899477958679
[36m(head, rank=0, pid=3476)[0m   }
[36m(head, rank=0, pid=3476)[0m }
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1019.03 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 7.13s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.71s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.47s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 1.26s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 330.29s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.13s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.58s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 6.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /checkpoints_s3/training_run_2_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1019.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1019.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1019.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1019.03s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.13s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.58s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 6.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 330.29s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.71s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.47s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 1.26s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 7.13s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1023.69s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1023.69s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1023.69s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1023.69s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1019.43 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 7.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.52s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 1.30s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 329.31s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 6.73s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_5.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1019.74 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 256.41s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 256.41s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 256.41s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 256.41s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 7.58s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.76s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.57s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 1.22s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 326.98s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.09s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.55s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 6.83s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_6.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1019.74 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 256.42s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 7.94s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.79s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.59s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 1.26s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 326.03s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.08s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.59s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 6.79s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /checkpoints_s3/training_run_5_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /checkpoints_s3/training_run_6_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /checkpoints_s3/training_run_7_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_5.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.84s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.84s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.84s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.84s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.73s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1019.74s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1019.74s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1019.74s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1019.74s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.09s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.55s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 6.83s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 326.98s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.76s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.57s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 1.22s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 7.58s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 256.41s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 256.41s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 256.41s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 256.41s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1024.31s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1024.31s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1024.31s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1024.31s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_5.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_6.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.85s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.85s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.85s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.85s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.71s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.71s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.71s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.71s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1019.74s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1019.74s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1019.74s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1019.74s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.08s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.59s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 6.79s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 326.03s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.79s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.59s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 1.26s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 7.94s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 256.42s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1024.31s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1024.31s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1024.31s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1024.31s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_6.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_4.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1019.91 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 256.42s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 7.48s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.75s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.50s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 1.41s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 327.43s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.09s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.55s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 6.64s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_7.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.89s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.89s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.89s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.89s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1019.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1019.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1019.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1019.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 6.73s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 329.31s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.52s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 1.30s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 7.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1024.10s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1024.10s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1024.10s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1024.10s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_7.json
[36m(head, rank=0, pid=3476)[0m Saved training info to: /checkpoints_s3/training_run_4_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_4.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.87s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.87s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.87s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.87s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.73s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1019.91s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1019.91s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1019.91s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1019.91s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.09s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.55s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 6.64s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 327.43s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.75s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.50s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 1.41s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 7.48s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 256.42s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1024.51s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1024.51s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1024.51s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1024.51s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_4.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_7.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1020.10 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 256.42s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 6.86s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.69s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.46s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.89s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 327.62s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.10s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.56s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 7.19s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /checkpoints_s3/training_run_7_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_7.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.91s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.91s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.91s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.91s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.73s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1020.10s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1020.10s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1020.10s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1020.10s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.10s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.56s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 7.19s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 327.62s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.69s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.46s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.89s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 6.86s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 256.42s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1024.74s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1024.74s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1024.74s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1024.74s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_7.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_2.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1020.50 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 256.41s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 256.41s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 256.41s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 256.41s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 7.72s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.77s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.55s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 1.19s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 324.92s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.06s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.58s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 6.85s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /checkpoints_s3/training_run_2_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_2.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.94s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.94s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.94s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.94s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.71s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.71s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.71s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.71s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1020.50s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1020.50s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1020.50s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1020.50s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.06s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.58s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 6.85s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 324.92s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.77s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.55s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 1.19s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 7.72s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 256.41s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 256.41s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 256.41s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 256.41s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1025.16s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1025.16s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1025.16s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1025.16s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_2.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 974.46 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 7.59s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.38s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 1.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 326.09s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 5.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /checkpoints_s3/training_run_0_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.92s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.92s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.92s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.92s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 50.39s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 50.39s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 50.39s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 50.39s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 974.46s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 974.46s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 974.46s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 974.46s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 5.99s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 326.09s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.38s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 1.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 7.59s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1027.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1027.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1027.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1027.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_0.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m JSON OUTPUT FROM MAIN PROCESS
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m --- Training Run 1 Info (Directory: /checkpoints_s3) ---
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "run_id": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "timestamp": "2025-08-03T17:37:18.924134",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "dataset_name": "open-r1/codeforces-cots",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "checkpoint_dir": "/checkpoints_s3",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "model_id": "google/gemma-3-12b-it",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "training_status": "completed",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "output_dir": "/checkpoints_s3",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "dataset_load_time": 2.9163553714752197,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "model_load_time": 50.38959217071533,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "training_time": 974.4571406841278,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_time": 1027.7630882263184,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "error": null,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "num_checkpoints_saved": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "average_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "min_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "max_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "individual_checkpoint_save_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     256.4325885772705
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   ],
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "num_batch_samples": 10,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_batch_sample_time": 7.585774898529053,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "average_batch_sample_time": 0.7585774898529053,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "min_batch_sample_time": 0.38161492347717285,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "max_batch_sample_time": 1.1249232292175293,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "individual_batch_sample_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.38161492347717285,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     1.1249232292175293,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.6855971813201904,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.9631485939025879,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.849433183670044,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.7540020942687988,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.817298412322998,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.7851555347442627,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.6345169544219971,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     0.5900847911834717
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   ],
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "num_training_steps": 80,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_training_step_time": 326.0889239311218,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "average_training_step_time": 4.076111549139023,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "min_training_step_time": 3.565412759780884,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "max_training_step_time": 5.989502906799316,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "individual_training_step_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.989502906799316,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7428712844848633,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.37501859664917,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.683372735977173,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.778231382369995,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.8719797134399414,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.95208740234375,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7108492851257324,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9591434001922607,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.650819778442383,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.8587749004364014,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.709937334060669,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.708345890045166,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.961254835128784,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7019591331481934,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.601150274276733,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.278624773025513,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7377731800079346,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9191088676452637,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7072691917419434,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.322515964508057,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.707996129989624,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.76556658744812,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.73671293258667,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.955469369888306,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.710594654083252,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.883965015411377,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7279014587402344,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.5707738399505615,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.716787815093994,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9377799034118652,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.376317262649536,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.815040588378906,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.886955976486206,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7371997833251953,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.089752435684204,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.541515111923218,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.383092403411865,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9771273136138916,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.8721561431884766,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7592103481292725,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7124574184417725,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.041784048080444,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.70985746383667,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9121642112731934,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9344985485076904,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.8409249782562256,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.907228946685791,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.926716089248657,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.823626518249512,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.8235504627227783,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7014496326446533,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9678738117218018,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.63020396232605,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.441364288330078,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.706336259841919,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     5.073802709579468,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.339477300643921,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.0363781452178955,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.113103628158569,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.565412759780884,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7540628910064697,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.897580862045288,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.401787757873535,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.8440637588500977,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.627556562423706,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.080700635910034,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.668180465698242,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.2671754360198975,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.843673944473267,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.892493724822998,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.9019508361816406,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.973700523376465,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.225417137145996,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.78933048248291,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     4.062950372695923,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.8711953163146973,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7113473415374756,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.986466884613037,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     3.7085719108581543
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   ]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m }
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m --- All Training Runs Summary ---
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "run_id": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "timestamp": "2025-08-03T17:37:18.924134",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_name": "open-r1/codeforces-cots",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_dir": "/checkpoints_s3",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_id": "google/gemma-3-12b-it",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_status": "completed",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "output_dir": "/checkpoints_s3",
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_time": 2.9163553714752197,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_time": 50.38959217071533,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_time": 974.4571406841278,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_time": 1027.7630882263184,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "error": null,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "num_checkpoints_saved": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "min_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "max_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "individual_checkpoint_save_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       256.4325885772705
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     ],
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "num_batch_samples": 10,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_batch_sample_time": 7.585774898529053,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_batch_sample_time": 0.7585774898529053,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "min_batch_sample_time": 0.38161492347717285,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "max_batch_sample_time": 1.1249232292175293,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "individual_batch_sample_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.38161492347717285,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       1.1249232292175293,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.6855971813201904,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.9631485939025879,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.849433183670044,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.7540020942687988,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.817298412322998,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.7851555347442627,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.6345169544219971,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       0.5900847911834717
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     ],
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "num_training_steps": 80,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_training_step_time": 326.0889239311218,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_training_step_time": 4.076111549139023,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "min_training_step_time": 3.565412759780884,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "max_training_step_time": 5.989502906799316,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "individual_training_step_times": [
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.989502906799316,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7428712844848633,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.37501859664917,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.683372735977173,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.778231382369995,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.8719797134399414,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.95208740234375,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7108492851257324,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9591434001922607,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.650819778442383,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.8587749004364014,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.709937334060669,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.708345890045166,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.961254835128784,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7019591331481934,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.601150274276733,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.278624773025513,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7377731800079346,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9191088676452637,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7072691917419434,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.322515964508057,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.707996129989624,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.76556658744812,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.73671293258667,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.955469369888306,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.710594654083252,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.883965015411377,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7279014587402344,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.5707738399505615,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.716787815093994,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9377799034118652,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.376317262649536,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.815040588378906,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.886955976486206,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7371997833251953,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.089752435684204,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.541515111923218,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.383092403411865,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9771273136138916,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.8721561431884766,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7592103481292725,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7124574184417725,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.041784048080444,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.70985746383667,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9121642112731934,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9344985485076904,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.8409249782562256,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.907228946685791,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.926716089248657,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.823626518249512,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.8235504627227783,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7014496326446533,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9678738117218018,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.63020396232605,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.441364288330078,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.706336259841919,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       5.073802709579468,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.339477300643921,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.0363781452178955,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.113103628158569,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.565412759780884,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7540628910064697,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.897580862045288,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.401787757873535,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.8440637588500977,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.627556562423706,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.080700635910034,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.668180465698242,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.2671754360198975,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.843673944473267,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.892493724822998,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.9019508361816406,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.973700523376465,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.225417137145996,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.78933048248291,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       4.062950372695923,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.8711953163146973,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7113473415374756,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.986466884613037,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m       3.7085719108581543
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     ]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   }
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ]
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m --- Timing Summary ---
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_runs": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "dataset_loading": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_average": 2.9163553714752197,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_min": 2.9163553714752197,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_max": 2.9163553714752197,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "dataset_load_total": 2.9163553714752197
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "model_loading": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_average": 50.38959217071533,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_min": 50.38959217071533,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_max": 50.38959217071533,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "model_load_total": 50.38959217071533
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "training": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_average": 974.4571406841278,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_min": 974.4571406841278,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_max": 974.4571406841278,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_total": 974.4571406841278
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "total_run_time": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_average": 1027.7630882263184,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_min": 1027.7630882263184,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_max": 1027.7630882263184,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_total": 1027.7630882263184
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "checkpoint_saving": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_checkpoints_saved": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_checkpoint_save_time": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_save_time_per_checkpoint": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_save_times_count": 1,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_save_min": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_save_max": 256.4325885772705,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "checkpoint_save_average": 256.4325885772705
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "batch_sampling": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_batch_samples": 10,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_batch_sample_time": 7.585774898529053,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_sample_time_per_batch": 0.7585774898529053,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "batch_sample_times_count": 10,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "batch_sample_min": 0.38161492347717285,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "batch_sample_max": 1.1249232292175293,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "batch_sample_average": 0.7585774898529053
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   },
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   "training_steps": {
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_training_steps": 80,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "total_training_step_time": 326.0889239311218,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "average_step_time_per_step": 4.076111549139023,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_step_times_count": 80,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_step_min": 3.565412759780884,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_step_max": 5.989502906799316,
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m     "training_step_average": 4.076111549139023
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   }
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m }
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_3.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1022.71 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 256.43s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 256.43s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 256.43s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 256.43s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 7.78s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.78s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.52s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 1.33s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 326.32s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.08s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.57s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 6.73s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /checkpoints_s3/training_run_3_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1022.50 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 7.70s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 1.26s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 328.51s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.11s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.56s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 6.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /checkpoints_s3/training_run_5_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_3.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.85s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.85s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.85s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.85s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.73s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.73s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1022.71s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1022.71s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1022.71s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1022.71s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.08s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.57s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 6.73s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 326.32s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.78s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.52s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 1.33s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 7.78s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 256.43s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 256.43s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 256.43s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 256.43s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1027.29s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1027.29s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1027.29s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1027.29s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_5.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1022.50s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1022.50s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1022.50s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1022.50s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.11s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.56s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 6.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 328.51s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.62s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 1.26s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 7.70s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1027.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1027.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1027.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1027.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_5.json
[36m(head, rank=0, pid=3476)[0m Chrome trace exported to: /tmp/trace_80_1.json
[36m(head, rank=0, pid=3476)[0m Completed Training in 1023.13 seconds
[36m(head, rank=0, pid=3476)[0m Checkpoint Save Statistics:
[36m(head, rank=0, pid=3476)[0m   - Number of checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   - Total checkpoint save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Average save time per checkpoint: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Min save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   - Max save time: 256.42s
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of batch samples: 10
[36m(head, rank=0, pid=3476)[0m   - Total batch sample time: 7.43s
[36m(head, rank=0, pid=3476)[0m   - Average batch sample time: 0.74s
[36m(head, rank=0, pid=3476)[0m   - Min batch sample time: 0.54s
[36m(head, rank=0, pid=3476)[0m   - Max batch sample time: 0.89s
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   - Number of training steps: 80
[36m(head, rank=0, pid=3476)[0m   - Total training step time: 326.42s
[36m(head, rank=0, pid=3476)[0m   - Average training step time: 4.08s
[36m(head, rank=0, pid=3476)[0m   - Min training step time: 3.56s
[36m(head, rank=0, pid=3476)[0m   - Max training step time: 7.16s
[36m(head, rank=0, pid=3476)[0m Training completed successfully for run 1
[36m(head, rank=0, pid=3476)[0m Saved training info to: /checkpoints_s3/training_run_1_1_info.json
[36m(head, rank=0, pid=3476)[0m Completed run 1/1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Saved overall training summary to: all_training_runs_summary_1.json
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m TRAINING PERFORMANCE SUMMARY
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Total Runs Completed: 1
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Dataset Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 2.91s
[36m(head, rank=0, pid=3476)[0m   • Min time: 2.91s
[36m(head, rank=0, pid=3476)[0m   • Max time: 2.91s
[36m(head, rank=0, pid=3476)[0m   • Total time: 2.91s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Model Loading Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1.72s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1.72s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1.72s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1.72s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Performance:
[36m(head, rank=0, pid=3476)[0m   • Average time: 1023.13s
[36m(head, rank=0, pid=3476)[0m   • Min time: 1023.13s
[36m(head, rank=0, pid=3476)[0m   • Max time: 1023.13s
[36m(head, rank=0, pid=3476)[0m   • Total time: 1023.13s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Training Step Performance:
[36m(head, rank=0, pid=3476)[0m   • Total training steps: 80
[36m(head, rank=0, pid=3476)[0m   • Average step time per step: 4.08s
[36m(head, rank=0, pid=3476)[0m   • Min step time: 3.56s
[36m(head, rank=0, pid=3476)[0m   • Max step time: 7.16s
[36m(head, rank=0, pid=3476)[0m   • Total training step time: 326.42s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Batch Sampling Performance:
[36m(head, rank=0, pid=3476)[0m   • Total batch samples: 10
[36m(head, rank=0, pid=3476)[0m   • Average sample time per batch: 0.74s
[36m(head, rank=0, pid=3476)[0m   • Min sample time: 0.54s
[36m(head, rank=0, pid=3476)[0m   • Max sample time: 0.89s
[36m(head, rank=0, pid=3476)[0m   • Total batch sample time: 7.43s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Checkpoint Saving Performance:
[36m(head, rank=0, pid=3476)[0m   • Total checkpoints saved: 1
[36m(head, rank=0, pid=3476)[0m   • Average save time per checkpoint: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Min save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Max save time: 256.42s
[36m(head, rank=0, pid=3476)[0m   • Total checkpoint save time: 256.42s
[36m(head, rank=0, pid=3476)[0m 
[36m(head, rank=0, pid=3476)[0m Overall Run Performance:
[36m(head, rank=0, pid=3476)[0m   • Average total time per run: 1027.77s
[36m(head, rank=0, pid=3476)[0m   • Min total time: 1027.77s
[36m(head, rank=0, pid=3476)[0m   • Max total time: 1027.77s
[36m(head, rank=0, pid=3476)[0m   • Total time across all runs: 1027.77s
[36m(head, rank=0, pid=3476)[0m ================================================================================
[36m(head, rank=0, pid=3476)[0m Saved timing summary to: timing_summary_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1023.21 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 7.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.47s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 1.26s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 327.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.09s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 6.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /checkpoints_s3/training_run_6_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.83s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.83s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.83s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.83s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1023.21s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1023.21s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1023.21s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1023.21s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.09s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.57s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 6.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 327.08s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.47s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 1.26s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 7.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1027.82s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1027.82s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1027.82s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1027.82s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_6.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1023.50 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 7.88s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 1.25s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 327.48s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.09s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.54s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 6.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /checkpoints_s3/training_run_1_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1023.65 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 7.74s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.55s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 1.47s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 328.49s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.11s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.59s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 6.55s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /checkpoints_s3/training_run_4_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.87s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.78s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1023.50s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1023.50s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1023.50s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1023.50s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.09s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.54s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 6.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 327.48s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.66s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 1.25s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 7.88s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 256.43s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1028.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1028.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1028.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1028.15s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_1.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.90s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.90s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.90s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.90s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1023.65s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1023.65s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1023.65s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1023.65s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.11s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.59s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 6.55s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 328.49s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.77s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.55s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 1.47s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 7.74s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 256.42s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1028.32s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1028.32s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1028.32s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1028.32s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_4.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Chrome trace exported to: /tmp/trace_80_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed Training in 1028.12 seconds
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Save Statistics:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total checkpoint save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average save time per checkpoint: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total batch sample time: 7.94s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average batch sample time: 0.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min batch sample time: 0.49s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max batch sample time: 1.51s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Number of training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Total training step time: 328.86s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Average training step time: 4.11s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Min training step time: 3.60s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   - Max training step time: 6.52s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training completed successfully for run 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved training info to: /checkpoints_s3/training_run_3_1_info.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Completed run 1/1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved overall training summary to: all_training_runs_summary_3.json
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m TRAINING PERFORMANCE SUMMARY
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Total Runs Completed: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Dataset Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 2.88s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 2.88s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 2.88s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 2.88s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Model Loading Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average time: 1028.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min time: 1028.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max time: 1028.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time: 1028.12s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Training Step Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training steps: 80
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average step time per step: 4.11s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min step time: 3.60s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max step time: 6.52s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total training step time: 328.86s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Batch Sampling Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch samples: 10
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average sample time per batch: 0.79s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min sample time: 0.49s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max sample time: 1.51s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total batch sample time: 7.94s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Checkpoint Saving Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoints saved: 1
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average save time per checkpoint: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total checkpoint save time: 256.44s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m 
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Overall Run Performance:
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Average total time per run: 1032.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Min total time: 1032.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Max total time: 1032.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m   • Total time across all runs: 1032.76s
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m ================================================================================
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m Saved timing summary to: timing_summary_3.json
[36m(head, rank=0, pid=3476)[0m cp: cannot create regular file '/checkpoints_s3/checkpoints/trace_10_2.json': File exists
[36m(head, rank=0, pid=3476)[0m skypilot: cached mount uploaded complete
[36m(worker1, rank=1, pid=2560, ip=10.102.30.168)[0m skypilot: cached mount uploaded complete
[0m[32m✓ Job finished (status: SUCCEEDED).[0m[0m
