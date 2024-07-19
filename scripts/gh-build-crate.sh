#!/bin/bash

trap 'echo "ERROR at line ${LINENO} (code: $?)" >&2' ERR
trap 'echo "Interrupted" >&2 ; exit 1' INT

set -o errexit
set -o nounset

# Import reusable bits
pushd $( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
. common.sh
popd

# Required for aliases to work in non-interactive scripts
shopt -s expand_aliases

# Ensure all alr runs are non-interactive and able to output unexpected errors
alias alr="alr -d -n --no-tty"

# Configure `sh` to be Bourne-compatible or some `configure` scripts may fail
[ `uname -s` == "Linux" ] && {
   sudo ln -fs $(type -p bash) /bin/sh
   sudo ln -fs $(type -p bash) /usr/bin/sh
   echo Configured Bash as default shell system-wide
}

# Disable check for ownership that sometimes confuses docker-run git
# Also, Github is not vulnerable to iCVE-2022-24765/CVE-2022-24767, see
# https://github.blog/2022-04-12-git-security-vulnerability-announced/
git config --global --add safe.directory '*'

# See whats happening
git log --graph --decorate --pretty=oneline --abbrev-commit --all | head -30

# Detect changes
CHANGES=$( changed_manifests )

# Bulk changes for the record
echo Changed files: $CHANGES

# Enable Homebrew on macOS
[ `uname -s` == "Darwin" ] && {
   eval $(brew shellenv)
   echo "Homebrew for macOS enabled"
}

# Disable assistant. This is necessary despite the setup-alire action doing it
# too, because we sometimes run inside a Docker with fresh configuration
alr toolchain --disable-assistant

# Show alr metadata
alr version

# Configure local index. For the case where two consecutive tests are attempted
# (e.g. macOS -/+ Homebrew), remove it first
if alr index | cut -f1 -d' ' | grep -q local; then
   alr index --del local || echo "Local index not yet configured, not removing"
fi
alr index --name local --add ./index

# Remove community index in case it has been added before
alr index --del community || true

# Show environment for the record
env

# Check index for obsolescent features
echo STRICT MODE index checks
alr index --check

echo INDEX CHECK no warnings during loading
# Check no warning during index loading, unless we are warning about a too old
# index version when using a development version of alr (which may be being
# tested with an old index)

# If '-' is found in alr --version, then it is a development version
if alr --version | grep -q '-' ; then
   alr settings --global --set warning.old_index false
fi

alr index --check 2>&1 | grep "Warning:" && exit 1

# Test crate
for file in $CHANGES; do

   if [[ $file == index.toml ]]; then
      echo Skipping index metadata file: $file
      continue
   fi

   if [[ $file != *.toml ]]; then
      echo Skipping non-crate file: $file
      continue
   fi

   if ! [ -f ./$file ]; then
      echo Skipping deleted file: $file
      continue
   fi

   if ! grep -q '^index/' <<< $file; then
      echo Skipping file outside of index hierarchy: $file
      continue
   fi

   # Checks passed, this is a crate we must test
   is_system=false

   crate=$(basename $file .toml | cut -f1 -d-)
   version=$(basename $file .toml | cut -f2- -d-)
   version_noextras=$(echo $version | cut -f1 -d- | cut -f1 -d+)
   milestone="$crate=$version"

   distro=$(alr version | grep distribution: | awk '{print $2}')

   echo
   box "$milestone"
   echo

   # Remember that version can be "external", in which case we do not know the
   # actual version, and indeed the test will only work if the external is the
   # newest version. This probably merits a way of being tested properly, but
   # that will require changes in alr.

   if [[ $version = external ]]; then
      echo Downgrading milestone to plain crate name
      milestone=$crate
   fi

   # Skip on explicit unavailability
   if alr show --system $milestone | grep -q 'Available when: False'; then
      echo SKIPPING crate build: $milestone UNAVAILABLE on system
      continue
   fi

   # This is a failure that should not happen.
   # TODO: remove when fixed in alr
   if alr show --system $milestone 2>&1 | grep -q 'Binary archive is unavailable'; then
      echo SKIPPING crate build: $milestone UNAVAILABLE on system
      continue
   fi

   # Show info for the record
   echo PLATFORM-INDEPENDENT CRATE INFO $milestone
   alr show $milestone
   alr show --external $milestone
   alr show --external-detect $milestone

   echo PLATFORM-DEPENDENT CRATE INFO $milestone
   alr show --system $milestone
   alr show --external --system $milestone
   alr show --external-detect --system $milestone
   crateinfo=$(alr show --external-detect --system $milestone)

   echo CRATE DEPENDENCIES $milestone
   alr show --solve --detail --external-detect $milestone
   echo CRATE DEPENDENCY TREE $milestone
   alr show --tree --external-detect $milestone

   solution=$(alr show --solve --detail --external-detect $milestone)

   # Fail if there are pins in the manifest
   if grep -q 'Pins (direct)' <<< $crateinfo ; then
      echo "FAIL: release $milestone manifest contains pins"
      exit 1
   fi

   # Skip if unavailable (this case is specific to binary crates with no
   # tarball for some combos of environment)
   if grep -q 'Origin: (unavailable on current platform)' <<< $crateinfo ; then
      echo SKIPPING build for crate $milestone with UNAVAILABLE binary origin
      apply_label "unavailable: $distro"
      continue
   fi

   # In unsupported platforms, externals are properly reported as missing. We
   # can skip testing of such a crate since it will likely fail.
   if grep -q 'Dependencies (missing):' <<< $solution ; then
      echo SKIPPING build for crate $milestone with MISSING external dependencies
      apply_label "missing dependencies: $distro"
      continue
   fi

   # Update system repositories whenever a detected system package is involved,
   # either as dependency or as the crate being tested.
   if grep -iq 'origin: system' <<< $solution; then
      echo "UPDATING system repositories with sudo from user ${USERNAME:-unset} ($UID:-unset)..."
      type apt-get 2>/dev/null && sudo apt-get update -y || true

      # In Arch case, eventually signatures become trustless and we need to refresh
      type pacman 2>/dev/null && sudo pacman -S archlinux-keyring --noconfirm || true # Won't apply to msys2?
      type pacman 2>/dev/null && sudo pacman -Syy --noconfirm || true
   else
      echo No need to update system repositories
   fi

   gnat_configured=let_it_be # default meaning to don't touch it

   # Install an Alire-provided gprbuild whenever there is a non-external gnat in solution
   if grep -iq 'gnat_' <<< $solution && ! grep -iq 'gnat_external' <<< $solution; then
      gnat_line=$(grep -E '^\s*gnat[a-z0-9_]*=\S*' <<< $solution | tail -1 | xargs)
      # -E for regex, -o for only the matched part, xargs to trim space

      echo "Detected GNAT information in solution: $gnat_line"
      gnat_dep=$(echo $gnat_line | awk '{print $1}')
      echo "Detected GNAT dependency in solution: $gnat_dep"

      # Consolidate gnat as gnat_native
      gnat_target=$(echo $gnat_dep | grep -Eo '^[^=]*')
      [ "$gnat_target" == "gnat" ] && gnat_target=gnat_native
      gnat_version=$(echo $gnat_dep | cut -f2 -d=)
      gnat_dep=${gnat_target:-gnat_native}=${gnat_version}
      echo "Final GNAT to be configured: ${gnat_dep}"

      if alr show $gnat_dep | grep 'Provides: gnat=' >/dev/null; then

         # Check whether a gnat has been already configured to then reset it afterwards
         if alr settings --global | grep -u toolchain.use.gnat=; then
            gnat_configured=$(alr settings --global | grep -u toolchain.use.gnat= | cut -f2 -d=)
         else
            gnat_configured=none
         fi

         echo "INSTALLING indexed toolchain compatible with $gnat_dep"
         echo "(Note: previously configured GNAT was $gnat_configured)"
         alr toolchain --select $gnat_dep gprbuild
         # We must give both the gnat in the solution and gprbuild, so both are compatible
         # Even if we default to gnat_native, that would select the appropriate gprbuild
      else
         echo "Dependency $gnat_dep is not a GNAT compiler dependency, treating it as a regular crate"
      fi
   fi

   # Detect whether the crate is binary to skip build
   is_binary=false
   if grep -iq 'binary archive' <<< $crateinfo; then
      echo Crate is BINARY
      is_binary=true
   fi

   # Alternatives for when the crate itself comes from an external. Only system
   # externals should be tested.
   if grep -q 'Origin: external path' <<< $crateinfo ; then
      echo SKIPPING detected external crate $milestone
      continue
   elif grep -q 'Origin: system package' <<< $crateinfo ; then
      echo INSTALLING detected system crate $milestone
      is_system=true
   elif grep -q 'Not found:' <<< $crateinfo && \
        grep -q 'There are external definitions' <<< $crateinfo
   then
      echo SKIPPING undetected external crate $crate
      continue
   fi

   # Detect missing dependencies for clearer error (probably unreaachable due
   # to previous check for missing externals)
   if grep -q 'Dependencies cannot be met' <<< $solution ; then
      echo "SKIPPING build of crate $milestone with MISSING regular dependencies"
      apply_label "missing dependencies: $distro"
      continue
   fi

   # Actual checks
   echo DEPLOYING CRATE $milestone
   if $is_binary; then
      echo SKIPPING BUILD for BINARY crate, FETCHING only
   elif $is_system; then
      echo SKIPPING BUILD for SYSTEM crate, FETCHING only
   fi

   alr -q get $milestone

   if $is_system; then
      echo DETECTING INSTALLED PACKAGE via crate $milestone
      alr show --external-detect $milestone
   elif $is_binary; then
      echo FETCHED BINARY crate OK
      # Clean-up latest test to free space for more tests to come in the same commit
      release_base=$(alr get --dirname $milestone)
      echo "Freeing up $(du -sh $release_base | cut -f1) used by $milestone"
      rm -rf ./$release_base
   else
      release_base=$(alr get --dirname $milestone)
      # This is where the release really is, even for monorepos

      release_deploy=$(echo "$release_base" | cut -f1 -d/ | cut -f1 -d'\')
      # This is where the repo is deployed, which for monorepos will not be the
      # same as the `release_base` above. It's always the top-level directory
      # of the `release_base` path

      echo FETCHED SOURCE crate OK, deployed at $release_base with root at $release_deploy

      # Enter the deployment dir silencing any warnings
      pushd $release_base

      echo "BUILD ENVIRONMENT (root crate)"
      alr printenv

      # To test, we use the regular `alr test`, reusing the download. In ths way,
      # crates providing a test action may succeed even if not intended to be
      # build directly.
      echo TESTING CRATE
      failed=false
      alr test || failed=true

      # echo Building with $(alr exec -- gnat --version) ...

      echo AVAILABLE LOGS
      ls -l alire/alr_test_*.log

      echo LOG CONTENTS
      ls alire/alr_test_*.log | while read file; do
         echo "---8<--- LOG FILE BEGIN: $file"
         cat $file
         echo "--->8--- LOG FILE END: $file"
      done

      [[ $failed == true ]] && { echo "Exiting with failed test"; exit 1; }

      echo LISTING EXECUTABLES of crate $milestone
      alr run --list

      popd

      # Clean-up latest test to free space for more tests to come in the same commit
      echo "Freeing up $(du -sh $release_deploy | cut -f1) used by $milestone at $release_deploy"
      rm -rf ./$release_deploy
   fi

   # Restore GNAT configuration if it was set before
   case $gnat_configured in
      let_it_be)
         echo "Toolchain configuration was not touched, doing nothing"
         ;;
      none)
         echo "Undoing toolchain configuration back to none"
         alr settings --global --unset toolchain.use.gnat
         alr settings --global --unset toolchain.use.gprbuild
         ;;
      *)
         echo "Restoring toolchain to $gnat_configured"
         alr toolchain --select $gnat_configured gprbuild
         ;;
   esac

   echo CRATE $milestone TEST ENDED SUCCESSFULLY
done
