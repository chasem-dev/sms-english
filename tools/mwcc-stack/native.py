#!/usr/bin/env python3
"""Dump GC/1.1 compiler objects through native Linux GDB and Wibo."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='source path relative to the repository')
    parser.add_argument('function', help='mangled function symbol')
    parser.add_argument('output', type=Path, help='empty output directory')
    parser.add_argument('--debugger', type=Path,
                        default=os.environ.get('MWCC_DEBUGGER'),
                        help='cadmic mwcc_debugger.py; defaults to MWCC_DEBUGGER')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.debugger is None or not args.debugger.is_file():
        parser.error('provide --debugger or set MWCC_DEBUGGER to mwcc_debugger.py')
    root = args.root.resolve()
    source = (root / args.source).resolve()
    if not source.is_file():
        parser.error('source file does not exist')
    source_relative = source.relative_to(root).as_posix()
    units = json.loads((root / 'objdiff.json').read_text())['units']
    unit = next((u for u in units if u.get('metadata', {}).get('source_path')
                 == source_relative), None)
    if unit is None:
        parser.error('source is not a configured objdiff unit')
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('output directory must be empty to preserve earlier dumps')
    output.mkdir(parents=True, exist_ok=True)
    compiler = root / 'build/compilers/GC/1.1/mwcceppc.exe'
    wibo = root / 'build/tools/wibo'
    if not compiler.is_file() or not wibo.is_file():
        parser.error('GC/1.1 compiler and Wibo must already be installed')
    commands = subprocess.check_output(
        [str(root / 'build/venv/bin/ninja'), '-t', 'commands', unit['base_path']],
        cwd=root, text=True)
    compile_args = None
    for command in commands.splitlines():
        tokens = shlex.split(command)
        if '&&' in tokens:
            tokens = tokens[:tokens.index('&&')]
        if '-c' in tokens and tokens[tokens.index('-c') + 1] == source_relative:
            executable = next((i for i, token in enumerate(tokens)
                               if token.endswith('/mwcceppc.exe')), None)
            if executable is not None:
                compile_args = tokens[executable + 1:]
    if compile_args is None:
        parser.error('could not find this unit\'s compiler command')
    # GC/1.1 cannot consume a GC/1.2.5 precompiled header. Preserve the
    # unit's optimization flags and include paths while compiling its source.
    for option, count in (('-prefix', 2), ('-MMD', 1)):
        if option in compile_args:
            index = compile_args.index(option)
            del compile_args[index:index + count]
    compile_args[compile_args.index('-o') + 1] = str(output)
    log = output / 'debugger.log'
    with tempfile.TemporaryDirectory(prefix='mwcc-native-') as temporary:
        temporary = Path(temporary)
        debugger = temporary / 'mwcc_debugger.py'
        debugger.write_bytes(args.debugger.read_bytes())
        subprocess.run([sys.executable, str(root / 'tools/mwcc-stack/patch-debugger.py'),
                        str(debugger)], check=True, stdout=subprocess.DEVNULL)
        script = temporary / 'native.gdb'
        script.write_text('''set pagination off
set confirm off
set debuginfod enabled off
break wibo::Executable::loadPE
run
finish
delete breakpoints
python
import importlib.util, sys
spec = importlib.util.spec_from_file_location('mwcc_debugger_native', %r)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.FUNCTION_NAME = %r
module.OUTPUT_DIR = %r
real_execute = gdb.execute
def native_execute(command, *args, **kwargs):
    if command in ('set architecture i386', 'set osabi none', 'target remote localhost:9001'):
        return None
    return real_execute(command, *args, **kwargs)
gdb.execute = native_execute
module.run_compiler()
end
''' % (str(debugger), args.function, str(output)))
        try:
            with log.open('w') as stream:
                result = subprocess.run(
                    ['gdb', '-batch', '-nx', '-x', str(script), '--args',
                     str(wibo), str(compiler)] + compile_args,
                    cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                    timeout=args.timeout)
        except subprocess.TimeoutExpired:
            sys.exit('Debugger timed out; see ' + str(log))
        if result.returncode or not (output / 'variables.txt').is_file():
            print('\n'.join(log.read_text().splitlines()[-20:]), file=sys.stderr)
            sys.exit('No complete compiler dump; see ' + str(log))
    print('GC/1.1 compiler dump: ' + str(output))
    print((output / 'variables.txt').read_text(), end='')


if __name__ == '__main__':
    main()
