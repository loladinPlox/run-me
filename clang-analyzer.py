#!/usr/bin/env python3.8

import re
import concurrent.futures
import os
import argparse
import logging
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import Dict, List
import subprocess
import shlex
import sys


def call(cmdline: List[str], **kwargs):
    logging.info(f"run: f{cmdline}")
    return subprocess.run(cmdline, **kwargs)


@dataclass
class CompileCommand:
    """Result of parsing a single entry in compile_commands.json"""

    cmdline: List[str]
    directory: str  # the compile directory
    path: str  # the c/c++ filename


class CompileCommands:
    """Parses compile_commands.json"""

    def __init__(self, path: str) -> None:
        @dataclass_json
        @dataclass
        class CompileCommandJSON:
            command: str
            directory: str
            file: str

        self._cmds: Dict[str, CompileCommand] = {}
        with open(path) as fd:
            js = CompileCommandJSON.schema().loads(fd.read(), many=True)
            for cmd in js:
                self._cmds[cmd.file] = CompileCommand(
                    cmdline=shlex.split(cmd.command),
                    directory=cmd.directory,
                    path=cmd.file,
                )

    def find(self, path: str) -> CompileCommand:
        """Find the ComplieCommand for the given source file. The path must exactly
        match a json entry. Raises exception if not found.

        """
        return self._cmds[path]


def llvm_binary_path(args, filename: str) -> str:
    if args.llvm_bin_dir:
        return os.path.join(args.llvm_bin_dir, filename)
    return filename


def generate_external_def_map(args: argparse.Namespace, paths: Dict[str, str]):
    non_test_paths = [path for path in paths.keys() if path.find("test.cc") < 0]
    with open("./externalDefMap.txt.org", "w") as out_fd:
        call(
            [llvm_binary_path(args, "clang-extdef-mapping-12"), "-p", "."]
            + sorted(non_test_paths),
            stdout=out_fd,
            universal_newlines=True,
        )
    with open("./externalDefMap.txt.org") as fd, open(
        "./externalDefMap.txt", "w"
    ) as out_fd:
        for line in fd.readlines():
            line = line.replace(f"{os.getcwd()}/", "")
            m = re.match(r"(.*) (\S+.cpp)$", line)
            if not m:
                raise Exception(f"illegal line: {line}")
            if m[1].find("c:@F@main#I#") >= 0:
                continue
            if m[2] not in paths:
                for p in paths:
                    print(p)
                raise Exception(f"{line}: file '{m[2]}' not found")
            out_fd.write(f"{m[1]} {paths[m[2]]}\n")


def main():
    def llvm_binary_path(filename: str) -> str:
        if args.llvm_bin_dir:
            return os.path.join(args.llvm_bin_dir, filename)
        return filename

    commands = CompileCommands(os.path.join(args.source_dir, "compile_commands.json"))

    ast_path_map: Dict[str, str] = {}
    cwd = os.getcwd()

    def compile_file(cc_path: str):
        ast_path = ast_path_map[cc_path]
        cmd = commands.find(cc_path)
        cmdline = [x for x in cmd.cmdline if x != "-c"]
        cmdline = (
            [llvm_binary_path("clang++")]
            + ["-emit-ast", "-o", os.path.join(cwd, ast_path)]
            + cmdline[1:]
        )
        call(cmdline, cwd=cmd.directory)

    if args.exclude:
        args.files = [x for x in args.files if not re.search(args.exclude, x)]

    # args.files = [os.path.abspath(x) for x in args.files]

    for cc_path in args.files:
        ast_path = cc_path + ".ast"
        ast_path_map[cc_path] = ast_path

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=16)
    results = []
    results.append(pool.submit(generate_external_def_map, args, ast_path_map))
    for cc_path in args.files:
        if cc_path.find("build_info") >= 0:
            continue
        results.append(pool.submit(compile_file, cc_path))
    for r in results:
        r.result()

    fixNeeded = ""
    for cc_path in args.files:
        logging.info(f"path: {cc_path}")
        cmd = commands.find(cc_path)
        cmdline = [x for x in cmd.cmdline if x != "-c"]
        cmdline = [
            llvm_binary_path("clang++"),
            "--analyze",
            "-Xclang",
            "-analyzer-config",
            "-Xclang",
            "experimental-enable-naive-ctu-analysis=true",
            "-Xclang",
            "-analyzer-config",
            "-Xclang",
            "ctu-dir=" + args.source_dir,
            "-Xclang",
            "-analyzer-output=plist-multi-file",
        ] + cmdline[1:]
        cmd_output = call(cmdline, cwd=cmd.directory, capture_output=True, text=True)

        if "error" in cmd_output.stderr \
            or "warning" in cmd_output.stderr \
                or "critical" in cmd_output.stderr:
            fixNeeded += f'{cmd_output.stderr}'

    if fixNeeded == "":
        return 0

    with open('results.txt', 'a') as output:
        output.write(fixNeeded)
    return -1


logging.basicConfig(level=logging.INFO)
parser = argparse.ArgumentParser()
parser.add_argument(
    "--source-dir",
    "-p",
    type=str,
    default=".",
    help="Directory that stores compile_commands.json. Must be set",
)
parser.add_argument(
    "--llvm-bin-dir",
    type=str,
    help="""The directory to
    find clang binaries. If omitted, they are looked up in
    PATH.""",
)
parser.add_argument(
    "--exclude",
    type=str,
    help="""Regexp of files to exclude. Matched using re.search""",
)
parser.add_argument(
    "files",
    nargs="+",
    type=str,
    help="""List of C++ source files to analyze. The pathnames must appear in compile_commands.json""",
)
args = parser.parse_args()

r = main()
sys.exit(1)