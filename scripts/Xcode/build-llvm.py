#!/usr/bin/env python

import hashlib
import fnmatch
import os
import platform
import re
import repo
import subprocess
import sys

from lldbbuild import *

#### SETTINGS ####

def LLVM_HASH_INCLUDES_DIFFS():
    return False

# For use with Xcode-style builds

def process_vcs(vcs):
    return {
        "svn": VCS.svn,
        "git": VCS.git
    }[vcs]

def process_root(name):
    return {
        "llvm": llvm_source_path(),
        "clang": clang_source_path(),
        "swift": swift_source_path(),
        "cmark": cmark_source_path(),
        "ninja": ninja_source_path()
    }[name]

def process_repo(r):
    return {
        'name': r["name"],
        'vcs': process_vcs(r["vcs"]),
        'root': process_root(r["name"]),
        'url': r["url"],
        'ref': r["ref"]
    }

def fallback_repo(name):
    return {
        'name': name,
        'vcs': None,
        'root': process_root(name),
        'url': None,
        'ref': None
    }

def dirs_exist(names):
    for name in names:
        if not os.path.isdir(process_root(name)):
            return False
    return True

def XCODE_REPOSITORIES():
    names = ["llvm", "clang", "swift", "cmark", "ninja"]
    if dirs_exist(names):
        return [fallback_repo(n) for n in names]
    override = repo.get_override()
    if override:
        return [process_repo(r) for r in override]
    identifier = repo.identifier()
    set = repo.find(identifier)
    return [process_repo(r) for r in set]


def get_c_compiler():
    return subprocess.check_output([
        'xcrun',
        '--sdk', 'macosx',
        '-find', 'clang'
    ]).rstrip()


def get_cxx_compiler():
    return subprocess.check_output([
        'xcrun',
        '--sdk', 'macosx',
        '-find', 'clang++'
    ]).rstrip()

#                 CFLAGS="-isysroot $(xcrun --sdk macosx --show-sdk-path) -mmacosx-version-min=${DARWIN_DEPLOYMENT_VERSION_OSX}" \
#                        LDFLAGS="-mmacosx-version-min=${DARWIN_DEPLOYMENT_VERSION_OSX}" \


def get_deployment_target():
    return os.environ.get('MACOSX_DEPLOYMENT_TARGET', None)


def get_c_flags():
    cflags = ''
    # sdk_path = subprocess.check_output([
    #     'xcrun',
    #     '--sdk', 'macosx',
    #     '--show-sdk-path']).rstrip()
    # cflags += '-isysroot {}'.format(sdk_path)

    deployment_target = get_deployment_target()
    if deployment_target:
        # cflags += ' -mmacosx-version-min={}'.format(deployment_target)
        pass

    return cflags


def get_cxx_flags():
    return get_c_flags()


def get_common_linker_flags():
    linker_flags = ""
    deployment_target = get_deployment_target()
    if deployment_target:
        # if len(linker_flags) > 0:
        #     linker_flags += ' '
        # linker_flags += '-mmacosx-version-min={}'.format(deployment_target)
        pass

    return linker_flags


def get_exe_linker_flags():
    return get_common_linker_flags()

def with_devices_preset_suffix():
    """Return a suffix for the LLDB-specific Swift build preset."""
    if os.environ.get("LLDB_SWIFT_STDLIB_INCLUDES_DEVICES", None) is not None:
        return "_with_devices"
    else:
        return ""

def BUILD_SCRIPT_FLAGS():
    return {
        "Debug": ["--preset=LLDB_Swift_ReleaseAssert" + with_devices_preset_suffix()],
        "DebugClang": ["--preset=LLDB_Swift_DebugAssert" + with_devices_preset_suffix()],
        "Release": ["--preset=LLDB_Swift_ReleaseAssert" + with_devices_preset_suffix()],
    }


def BUILD_SCRIPT_ENVIRONMENT():
    return {
        "SWIFT_SOURCE_ROOT": lldb_source_path(),
        "SWIFT_BUILD_ROOT": llvm_build_dirtree()
    }

#### COLLECTING ALL ARCHIVES ####


def collect_archives_in_path(path):
    files = os.listdir(path)
    # Only use libclang and libLLVM archives (and gtests), and exclude libclang_rt.
    # Also include swigt and cmark.
    # This is not a very scalable solution.  Direct dependency determination would
    # be preferred.
    regexp = "^lib(clang[^_]|LLVM|gtest|swift|cmark).*$"
    return [
        os.path.join(
            path,
            file) for file in files if file.endswith(".a") and re.match(
            regexp,
            file)]


def archive_list():
    paths = library_paths()
    archive_lists = [collect_archives_in_path(path) for path in paths]
    return [archive for archive_list in archive_lists for archive in archive_list]


def write_archives_txt():
    f = open(archives_txt(), 'w')
    for archive in archive_list():
        f.write(archive + "\n")
    f.close()

#### COLLECTING REPOSITORY MD5S ####


def source_control_status(spec):
    vcs_for_spec = vcs(spec)
    if LLVM_HASH_INCLUDES_DIFFS():
        return vcs_for_spec.status() + vcs_for_spec.diff()
    else:
        return vcs_for_spec.status()


def source_control_status_for_specs(specs):
    statuses = [source_control_status(spec) for spec in specs]
    return "".join(statuses)


def all_source_control_status():
    return source_control_status_for_specs(XCODE_REPOSITORIES())


def md5(string):
    m = hashlib.md5()
    m.update(string)
    return m.hexdigest()


def all_source_control_status_md5():
    return md5(all_source_control_status())

#### CHECKING OUT AND BUILDING LLVM ####


def apply_patches(spec):
    files = os.listdir(os.path.join(lldb_source_path(), 'scripts'))
    patches = [
        f for f in files if fnmatch.fnmatch(
            f, spec['name'] + '.*.diff')]
    for p in patches:
        run_in_directory(["patch",
                          "-p0",
                          "-i",
                          os.path.join(lldb_source_path(),
                                       'scripts',
                                       p)],
                         spec['root'])


def check_out_if_needed(spec):
    if (build_type() != BuildType.CustomSwift) and not (
            os.path.isdir(spec['root'])):
        vcs(spec).check_out()
        apply_patches(spec)


def all_check_out_if_needed():
    map(check_out_if_needed, XCODE_REPOSITORIES())


def should_build_llvm():
    if build_type() == BuildType.CustomSwift:
        return False
    if build_type() == BuildType.Xcode:
        # TODO use md5 sums
        return True


def do_symlink(source_path, link_path):
    print "Symlinking " + source_path + " to " + link_path
    if os.path.islink(link_path):
        os.remove(link_path)
    if not os.path.exists(link_path):
        os.symlink(source_path, link_path)


def setup_source_symlink(repo):
    source_path = repo["root"]
    link_path = os.path.join(lldb_source_path(), os.path.basename(source_path))
    do_symlink(source_path, link_path)


def setup_source_symlinks():
    map(setup_source_symlink, XCODE_REPOSITORIES())


def setup_build_symlink():
    source_path = package_build_path()
    link_path = expected_package_build_path()
    do_symlink(source_path, link_path)

def find_cmake():
    # First check the system PATH env var for cmake
    cmake_binary = find_executable_in_paths(
        "cmake", os.environ["PATH"].split(os.pathsep))
    if cmake_binary:
        # We found it there, use it.
        return cmake_binary

    # Check a few more common spots.  Xcode launched from Finder
    # will have the default environment, and may not have
    # all the normal places present.
    extra_cmake_dirs = [
        "/usr/local/bin",
        "/opt/local/bin",
        os.path.join(os.path.expanduser("~"), "bin")
    ]

    if platform.system() == "Darwin":
        # Add locations where an official CMake.app package may be installed.
        extra_cmake_dirs.extend([
            os.path.join(
                os.path.expanduser("~"),
                "Applications",
                "CMake.app",
                "Contents",
                "bin"),
            os.path.join(
                os.sep,
                "Applications",
                "CMake.app",
                "Contents",
                "bin")])

    cmake_binary = find_executable_in_paths("cmake", extra_cmake_dirs)
    if cmake_binary:
        # We found it in one of the usual places.  Use that.
        return cmake_binary

    # We couldn't find cmake.  Tell the user what to do.
    raise Exception(
        "could not find cmake in PATH ({}) or in any of these locations ({}), "
        "please install cmake or add a link to it in one of those locations".format(
            os.environ["PATH"], extra_cmake_dirs))


def cmake_flags():
    cmake_flags = CMAKE_FLAGS()[lldb_configuration()]
    cmake_flags += ["-GNinja",
                    "-DCMAKE_C_COMPILER={}".format(get_c_compiler()),
                    "-DCMAKE_CXX_COMPILER={}".format(get_cxx_compiler()),
                    "-DCMAKE_INSTALL_PREFIX={}".format(expected_package_build_path_for("llvm")),
                    "-DCMAKE_C_FLAGS={}".format(get_c_flags()),
                    "-DCMAKE_CXX_FLAGS={}".format(get_cxx_flags()),
                    "-DCMAKE_EXE_LINKER_FLAGS={}".format(get_exe_linker_flags()),
                    "-DCMAKE_SHARED_LINKER_FLAGS={}".format(get_shared_linker_flags()),
                    "-DHAVE_CRASHREPORTER_INFO=1"]
    deployment_target = get_deployment_target()
    if deployment_target:
        cmake_flags.append(
            "-DCMAKE_OSX_DEPLOYMENT_TARGET={}".format(deployment_target))
    return cmake_flags


def run_cmake(cmake_build_dir, ninja_binary_path):
    cmake_binary = find_cmake()
    print "found cmake binary: using \"{}\"".format(cmake_binary)

    command_line = [cmake_binary] + cmake_flags() + [
        "-DCMAKE_MAKE_PROGRAM={}".format(ninja_binary_path),
        llvm_source_path()]
    print "running cmake like so: ({}) in dir ({})".format(command_line, cmake_build_dir)

    subprocess.check_call(
        command_line,
        cwd=cmake_build_dir,
        env=cmake_environment())


def create_directories_as_needed(path):
    try:
        os.makedirs(path)
    except OSError as error:
        # An error indicating that the directory exists already is fine.
        # Anything else should be passed along.
        if error.errno != errno.EEXIST:
            raise error


def run_cmake_if_needed(ninja_binary_path):
    cmake_build_dir = package_build_path()
    if should_run_cmake(cmake_build_dir):
        # Create the build directory as needed
        create_directories_as_needed(cmake_build_dir)
        run_cmake(cmake_build_dir, ninja_binary_path)


def build_ninja_if_needed():
    # First check if ninja is in our path.  If so, there's nothing to do.
    ninja_binary_path = find_executable_in_paths(
        "ninja", os.environ["PATH"].split(os.pathsep))
    if ninja_binary_path:
        # It's on the path.  cmake will find it.  We're good.
        print "found ninja here: \"{}\"".format(ninja_binary_path)
        return ninja_binary_path

    # Figure out if we need to build it.
    ninja_build_dir = ninja_source_path()
    ninja_binary_path = os.path.join(ninja_build_dir, "ninja")
    if not is_executable(ninja_binary_path):
        # Build ninja
        command_line = ["python", "configure.py", "--bootstrap"]
        print "building ninja like so: ({}) in dir ({})".format(command_line, ninja_build_dir)
        subprocess.check_call(
            command_line,
            cwd=ninja_build_dir,
            env=os.environ)

    return ninja_binary_path

def build_script_flags():
    return BUILD_SCRIPT_FLAGS()[lldb_configuration(
    )] + ["swift_install_destdir=" + expected_package_build_path_for("swift")]


def join_dicts(dict1, dict2):
    d = dict1.copy()
    d.update(dict2)
    return d


def build_script_path():
    return os.path.join(swift_source_path(), "utils", "build-script")


def build_script_environment():
    return join_dicts(os.environ, BUILD_SCRIPT_ENVIRONMENT())


def build_llvm():
    subprocess.check_call(["python",
                           build_script_path()] + build_script_flags(),
                          cwd=lldb_source_path(),
                          env=build_script_environment())


def build_llvm_if_needed():
    if should_build_llvm():
        setup_source_symlinks()
        build_llvm()
        setup_build_symlink()

#### MAIN LOGIC ####

if __name__ == "__main__":
    all_check_out_if_needed()
    build_llvm_if_needed()
    write_archives_txt()
    sys.exit(0)
