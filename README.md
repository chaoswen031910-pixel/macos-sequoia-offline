# macOS Sequoia 15.8.1 installer image builder

This project builds a full macOS Sequoia **15.8.1 (24H32)** USB installer image on a standard, disposable GitHub-hosted Intel Mac. It downloads Apple's full InstallAssistant package, checks its size and Apple signature, verifies the macOS version inside SharedSupport, runs Apple's createinstallmedia, compares the copied system payload, checks the filesystems and GPT, and computes SHA-256 checksums.

The final raw image is 24 GiB, with GPT, a normal EFI partition, a 1 GiB FAT32 `OCFILES` support volume, and an HFS+ installer volume. It fits a 32 GB SanDisk with a measured capacity of 30,765,219,840 bytes. The compressed image is split into independently downloadable parts below 2 GiB.

## Running

The workflow runs when its files are pushed or when started manually in Actions. It only runs in the public `chaoswen031910-pixel/macos-sequoia-offline` repository, uses a standard `macos-15-intel` runner, and retains output artifacts for one day. It does not enable paid runners, add payment methods, or change account spending settings. GitHub storage limits can still block uploading a large result; storage eligibility must be checked before execution.

The workflow only contains generic build files. Do not upload an existing EFI, hardware profile, local logs, passwords, or other machine-specific files. The image intentionally contains no OpenCore configuration: restore the already verified EFI locally after writing the image, and copy the local driver packages to OCFILES.

## Downloading and verifying

Download `installer-metadata` and all available `installer-part-NN` artifacts from the successful run. Extract all ZIPs into one folder, so manifest.json, assemble-image.py, and the `.part001`, `.part002`, etc. files are together.

Use Python 3 on Windows to run:

```text
python assemble-image.py --manifest manifest.json --parts . --output sequoia-15.8.1.img
```

The assembly tool verifies every part and the compressed and raw SHA-256 checksums before publishing the .img file. It writes ordinary files only; it cannot format or write to a physical disk. At least 25 GiB of additional free local storage is required for the raw image.

Do not write the image until the target USB has been identified again by its model, capacity, and serial number and its current contents have been backed up. Windows disk numbers may change. The existing Windows system disk and the external installation-target SSD are not image-writing targets.

## Limits

The build has not yet been executed on GitHub. Successful construction verifies the install media, not this laptop's hardware compatibility, graphics acceleration, or complete offline installation. Apple's bootable-installer documentation still describes possible network requirements for firmware and model-specific information.

Sources: [Apple bootable installer instructions](https://support.apple.com/en-us/101578), [GitHub hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners), [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
