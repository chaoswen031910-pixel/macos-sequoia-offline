#!/bin/bash
set -euo pipefail

# These cleanup operations are restricted to a disposable GitHub-hosted Mac.
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_OS:-}" == macOS && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]]
[[ "${REPOSITORY_VISIBILITY:-}" == public ]]
[[ "$(uname -m)" == x86_64 ]]

script_dir="$(cd "$(dirname "$0")" && pwd)"
work_dir="$(mktemp -d "$RUNNER_TEMP/sequoia-offline.XXXXXX")"
output_dir="$GITHUB_WORKSPACE/output"
python_bin="$(command -v python3)"
"$python_bin" --version

image_device=""
shared_mount=""
cleanup() {
  if [[ -n "$shared_mount" ]]; then hdiutil detach "$shared_mount" || true; fi
  if [[ -n "$image_device" ]]; then hdiutil detach "$image_device" || true; fi
}
trap cleanup EXIT

# Installer + writable image + raw export can require over 50 GiB simultaneously.
# Remove only preinstalled Xcode apps from this disposable runner, never user disks.
sudo xcode-select --switch /Library/Developer/CommandLineTools
for xcode_app in /Applications/Xcode*.app; do
  [[ -e "$xcode_app" || -L "$xcode_app" ]] || continue
  [[ "$xcode_app" == /Applications/Xcode*.app ]]
  sudo rm -rf -- "$xcode_app"
done
"$python_bin" -c 'import shutil,sys; free=shutil.disk_usage(sys.argv[1]).free; print(f"Free space: {free/2**30:.1f} GiB"); sys.exit(0 if free >= 60*2**30 else "Need at least 60 GiB free; stopping")' "$work_dir"

package_url='https://swcdn.apple.com/content/downloads/07/04/142-27431-A_VX0FXASAKC/m7trj81qnvp94e9nzr06e7qi9lqey7cwmu/InstallAssistant.pkg'
package_path="$work_dir/InstallAssistant.pkg"
curl --fail --location --proto '=https' --proto-redir '=https' --retry 8 --retry-all-errors --retry-delay 10 --connect-timeout 30 --continue-at - --output "$package_path" "$package_url"
[[ "$(stat -f '%z' "$package_path")" == 15664523626 ]]
pkgutil --check-signature "$package_path" | tee "$work_dir/package-signature.txt"
grep -q 'Apple' "$work_dir/package-signature.txt"
"$python_bin" - "$package_path" "$package_url" "$script_dir" > "$work_dir/package.json" <<'PY'
import json, pathlib, sys
sys.path.insert(0, sys.argv[3])
from installer_utils import sha256
path = pathlib.Path(sys.argv[1])
print(json.dumps({'url': sys.argv[2], 'size': path.stat().st_size, 'sha256': sha256(path), 'signature_verified': True}, indent=2))
PY
sudo installer -pkg "$package_path" -target /
installer_app='/Applications/Install macOS Sequoia.app'
[[ -x "$installer_app/Contents/Resources/createinstallmedia" ]]
shared_source="$installer_app/Contents/SharedSupport/SharedSupport.dmg"
[[ -f "$shared_source" && "$(stat -f '%z' "$shared_source")" -gt 12000000000 ]]
hdiutil verify "$shared_source"
shared_mount="$work_dir/shared-source"
mkdir "$shared_mount"
hdiutil attach -readonly -nobrowse -mountpoint "$shared_mount" "$shared_source"
"$python_bin" "$script_dir/installer_utils.py" assets "$shared_mount" | tee "$work_dir/installer-version.json"
hdiutil detach "$shared_mount"
shared_mount=""
rm -- "$package_path"

# 24 GiB fits the serial-verified 30,765,219,840-byte SanDisk.
# GPT includes its normal 200 MiB EFI plus a 1 GiB FAT32 support volume.
hdiutil create -size 24g -type SPARSE -layout GPTSPUD -fs HFS+J -volname INSTALLER "$work_dir/installer"
sparse_image="$work_dir/installer.sparseimage"
hdiutil attach -nobrowse -nomount -plist "$sparse_image" > "$work_dir/attached.plist"
image_device="$("$python_bin" - "$work_dir/attached.plist" <<'PY'
import plistlib,re,sys
with open(sys.argv[1], 'rb') as f: metadata=plistlib.load(f)
devices=[x['dev-entry'] for x in metadata['system-entities'] if re.fullmatch(r'/dev/disk[0-9]+', x.get('dev-entry',''))]
assert len(devices)==1, devices
print(devices[0])
PY
)"
# The device must belong to the exact newly created disk-image file.
hdiutil info -plist > "$work_dir/images.plist"
"$python_bin" - "$work_dir/images.plist" "$sparse_image" "$image_device" <<'PY'
import pathlib,plistlib,sys
with open(sys.argv[1],'rb') as f: images=plistlib.load(f)['images']
match=[x for x in images if pathlib.Path(x['image-path']).resolve()==pathlib.Path(sys.argv[2]).resolve()]
assert len(match)==1
assert any(e.get('dev-entry')==sys.argv[3] for e in match[0]['system-entities'])
PY
diskutil partitionDisk "$image_device" GPT MS-DOS OCFILES 1g JHFS+ INSTALLER R
sudo "$installer_app/Contents/Resources/createinstallmedia" --volume /Volumes/INSTALLER --nointeraction
installed_volume='/Volumes/Install macOS Sequoia'
copied_shared="$installed_volume/Install macOS Sequoia.app/Contents/SharedSupport/SharedSupport.dmg"
[[ -f "$copied_shared" ]]
cmp "$shared_source" "$copied_shared"
diskutil verifyDisk "$image_device"
diskutil verifyVolume "$installed_volume"
diskutil list "$image_device" | tee "$work_dir/partition-layout.txt"
hdiutil detach "$image_device"
image_device=""

hdiutil convert "$sparse_image" -format UDTO -o "$work_dir/sequoia-15.8.1"
raw_image="$work_dir/sequoia-15.8.1.img"
mv "$work_dir/sequoia-15.8.1.cdr" "$raw_image"
"$python_bin" "$script_dir/installer_utils.py" gpt "$raw_image" > "$work_dir/raw-image.json"
# Discard only the build's temporary sparse image before compression.
rm -- "$sparse_image"
gzip -1 "$raw_image"
"$python_bin" "$script_dir/installer_utils.py" split "$raw_image.gz" "$work_dir/raw-image.json" "$work_dir/package.json" "$output_dir"
cp "$work_dir/package-signature.txt" "$work_dir/installer-version.json" "$work_dir/partition-layout.txt" "$output_dir/"
cp "$script_dir/assemble-image.py" "$output_dir/"
cp "$GITHUB_WORKSPACE/README.md" "$output_dir/README.md"
printf 'Verified macOS Sequoia 15.8.1 (24H32) installer image; EFI must be restored separately.\n' >> "$GITHUB_STEP_SUMMARY"
