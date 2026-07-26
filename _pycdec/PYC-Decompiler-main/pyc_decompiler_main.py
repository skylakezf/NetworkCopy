import os
import sys
import argparse
import traceback
import struct
import importlib
import marshal
import time
import py_compile
import re
import dis

def get_python_version_from_pyc(pyc_file):
    try:
        with open(pyc_file, 'rb') as f:
            magic = f.read(4)
            
        magic_to_version = {
            3310: (3, 4),
            3350: (3, 5), 
            3390: (3, 6),
            3400: (3, 7),
            3430: (3, 8), 
            3450: (3, 9),
            3490: (3, 10),
            3500: (3, 11),
            3510: (3, 12),
            3570: (3, 13),
            3571: (3, 13),
        }
        
        magic_int = struct.unpack('<H', magic[:2])[0]
        
        closest_magic = min(magic_to_version.keys(), key=lambda x: abs(x - magic_int))
        
        if abs(closest_magic - magic_int) < 50:
            detected_version = magic_to_version[closest_magic]
            print(f"Detected Python {detected_version[0]}.{detected_version[1]} bytecode")
            return detected_version
        else:
            print(f"Unknown magic number: {magic_int} (0x{magic_int:04x})")
            return None
    except Exception as e:
        print(f"Error detecting Python version: {str(e)}")
        return None

def pyc_to_py(pyc_file, output_file=None, force=False, use_alternative=False, create_pseudo=False):
    if not os.path.exists(pyc_file):
        print(f"Error: File '{pyc_file}' does not exist.")
        return False
    
    if not pyc_file.endswith('.pyc'):
        print(f"Warning: File '{pyc_file}' does not have a .pyc extension.")
    
    if output_file is None:
        if pyc_file.endswith('.pyc'):
            output_file = pyc_file[:-4] + '.py'
        else:
            output_file = pyc_file + '.py'
    
    if os.path.exists(output_file) and not force:
        print(f"Error: Output file '{output_file}' already exists. Use --force to overwrite.")
        return False
    
    pseudo_file = None
    if create_pseudo:
        if output_file.endswith('.py'):
            pseudo_file = output_file[:-3] + '_pseudo.py'
        else:
            pseudo_file = output_file + '_pseudo.py'
            
        if os.path.exists(pseudo_file) and not force:
            print(f"Error: Pseudo-code file '{pseudo_file}' already exists. Use --force to overwrite.")
            return False
    
    py_version = get_python_version_from_pyc(pyc_file)
    
    if py_version and py_version[0] == 3 and py_version[1] >= 12:
        print(f"Detected Python {py_version[0]}.{py_version[1]} bytecode - using specialized handling")
        return handle_modern_python_pyc(pyc_file, output_file, py_version, pseudo_file)
    
    success = False
    try:
        import uncompyle6
        
        print(f"Attempting to decompile '{pyc_file}' with uncompyle6...")
        with open(output_file, 'w') as f:
            uncompyle6.decompile_file(pyc_file, f)
        print(f"Successfully converted '{pyc_file}' to '{output_file}'")
        success = True
        
        if create_pseudo and success:
            generate_pseudo_code(pyc_file, pseudo_file, py_version)
            
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"Error decompiling '{pyc_file}' with uncompyle6: {str(e)}")
        print(f"Detailed error information:\n{error_details}")
        
        if use_alternative:
            success = try_alternative_decompilers(pyc_file, output_file, pseudo_file if create_pseudo else None)
    
    return success

def handle_modern_python_pyc(pyc_file, output_file, py_version, pseudo_file=None):
    py_ver_str = f"{py_version[0]}.{py_version[1]}"
    print(f"Processing Python {py_ver_str} bytecode file...")
    
    try:
        print(f"Trying pycdc for Python {py_ver_str} file...")
        import subprocess
        result = subprocess.run(['pycdc', pyc_file], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            with open(output_file, 'w') as f:
                f.write(result.stdout)
            print(f"Successfully converted Python {py_ver_str} file '{pyc_file}' to '{output_file}' using pycdc")
            
            if pseudo_file:
                generate_pseudo_code(pyc_file, pseudo_file, py_version)
                
            return True
        else:
            print(f"pycdc failed or produced empty output: {result.stderr}")
    except Exception as e:
        print(f"Error using pycdc: {str(e)}")

    try:
        print(f"Using specialized Python {py_ver_str} disassembly approach...")
        with open(output_file, 'w') as f:
            f.write(f"# Disassembly of Python {py_ver_str} file: {pyc_file}\n")
            f.write("# Note: This is a disassembly since direct decompilation for this Python version is not supported\n")
            f.write("# You may need to manually reconstruct the Python code from this information\n\n")
            
            with open(pyc_file, 'rb') as pyc:
                pyc.seek(16)  
                try:
                    code = marshal.load(pyc)
                    f.write("# Successfully extracted code object\n\n")
                    
                    f.write(f"# Using Python {sys.version} for disassembly\n\n")
                    
                    f.write("# String constants found in code:\n")
                    str_constants = []
                    for const in code.co_consts:
                        if isinstance(const, str) and const:
                            str_constants.append(const)
                            f.write(f"# '{const}'\n")
                    f.write("\n")
                    
                    f.write("# Full Disassembly:\n")
                    old_stdout = sys.stdout
                    sys.stdout = f
                    dis.dis(code)
                    sys.stdout = old_stdout
                    
                    f.write("\n\n# Code Object Information:\n")
                    f.write(f"# co_name: {code.co_name}\n")
                    f.write(f"# co_filename: {code.co_filename}\n")
                    f.write(f"# co_firstlineno: {code.co_firstlineno}\n")
                    f.write(f"# co_names: {code.co_names}\n")
                    f.write(f"# co_varnames: {code.co_varnames}\n")
                    
                    f.write("\n\n# Reconstructed Basic Structure:\n")                    
                    imports = []
                    for name in code.co_names:
                        if name in sys.modules or name in ['os', 'sys', 'time', 're', 'math', 'random', 'json', 
                                                         'collections', 'itertools', 'functools', 'socket', 'threading']:
                            imports.append(f"import {name}")
                    
                    if imports:
                        f.write("# Detected imports:\n")
                        f.write("\n".join(imports) + "\n\n")
                    
                    functions = set()
                    classes = set()
                    variables = set()
                    
                    for name in code.co_names:
                        if name[0].isupper() and len(name) > 1:
                            classes.add(name)
                        elif name[0].islower() and not name.startswith('__'):
                            functions.add(name)
                        elif not name.startswith('__'):
                            variables.add(name)
                    
                    if classes:
                        f.write("# Detected classes:\n")
                        for cls in classes:
                            f.write(f"# class {cls}:\n#     ...\n\n")
                    
                    if functions:
                        f.write("# Detected functions:\n")
                        for func in functions:
                            f.write(f"# def {func}():\n#     ...\n\n")
                    
                    if hasattr(code, 'co_code'):
                        f.write("# Attempted code reconstruction (highly speculative):\n")
                        try:
                            bytecode = code.co_code
                            if len(bytecode) > 0:
                                f.write("# Basic structural elements detected\n")
                                
                                if b"__name__" in bytecode or "__name__" in code.co_names:
                                    f.write("\n# Main entry pattern detected:\n")
                                    f.write("if __name__ == \"__main__\":\n    # Main code execution\n")
                        except:
                            f.write("# Could not analyze bytecode further\n")
                    
                    f.write("\n\n# IMPORTANT: Python {py_ver_str} bytecode decompilation support is limited\n")
                    f.write(f"# For better results with Python {py_ver_str}, consider:\n")
                    f.write("# 1. Using a dedicated Python {py_ver_str} decompiler when available\n")
                    f.write("# 2. Requesting the original source code if possible\n")
                    f.write("# 3. Manual reconstruction based on this disassembly\n")
                    
                except Exception as e:
                    f.write(f"# Error extracting code object: {str(e)}\n")
                    traceback_str = traceback.format_exc()
                    f.write(f"# Traceback: {traceback_str}\n")
                    
                    f.write("\n# Attempting direct bytecode analysis:\n")
                    pyc.seek(0)
                    bytecode = pyc.read()
                    f.write(f"# File size: {len(bytecode)} bytes\n")
                    
                    try:
                        strings = re.findall(b'[\x20-\x7E]{3,}', bytecode)
                        if strings:
                            f.write("\n# Detected strings in bytecode:\n")
                            for s in strings:
                                try:
                                    decoded = s.decode('utf-8')
                                    if re.match(r'^[a-zA-Z0-9_.\-+*/\\:;,<>?!@#$%^&*()[\]{}]+$', decoded):
                                        f.write(f"# {decoded}\n")
                                except:
                                    pass
                    except Exception as string_error:
                        f.write(f"# Error analyzing strings: {str(string_error)}\n")
        
        print(f"Basic Python {py_ver_str} analysis saved to '{output_file}'. Manual reconstruction may be needed.")
        
        if pseudo_file:
            print(f"Generating pseudo-code for Python {py_ver_str} file...")
            generate_pseudo_code(pyc_file, pseudo_file, py_version)
            
        return True
    except Exception as e:
        print(f"All Python {py_ver_str} decompilation methods failed: {str(e)}")
        traceback.print_exc()
        return False

def try_alternative_decompilers(pyc_file, output_file, pseudo_file):
    try:
        print("Attempting with alternative decompiler: decompyle3...")
        import decompyle3.main
        decompyle3.main.decompile_file(pyc_file, output_file)
        print(f"Successfully converted '{pyc_file}' to '{output_file}' using decompyle3")
        return True
    except Exception as e:
        print(f"decompyle3 also failed: {str(e)}")
    
    try:
        print("Attempting with alternative decompiler: pycdc...")
        import subprocess
        result = subprocess.run(['pycdc', pyc_file], capture_output=True, text=True)
        if result.returncode == 0:
            with open(output_file, 'w') as f:
                f.write(result.stdout)
            print(f"Successfully converted '{pyc_file}' to '{output_file}' using pycdc")
            return True
        else:
            print(f"pycdc failed: {result.stderr}")
    except Exception as e:
        print(f"Error using pycdc: {str(e)}")
    
    try:
        print("Attempting basic disassembly as a last resort...")
        import dis
        import marshal
        import importlib.util
        
        with open(output_file, 'w') as f:
            f.write(f"# Disassembly of {pyc_file}\n")
            f.write("# Note: This is a fallback disassembly since decompilation failed\n")
            f.write("# You may need to manually reconstruct the Python code from this information\n\n")
            
            with open(pyc_file, 'rb') as pyc:
                pyc.seek(16)
                try:
                    code = marshal.load(pyc)
                    f.write("# Successfully extracted code object\n\n")
                    
                    old_stdout = sys.stdout
                    sys.stdout = f
                    dis.dis(code)
                    sys.stdout = old_stdout
                    
                    f.write("\n\n# Code Object Information:\n")
                    f.write(f"# co_name: {code.co_name}\n")
                    f.write(f"# co_filename: {code.co_filename}\n")
                    f.write(f"# co_firstlineno: {code.co_firstlineno}\n")
                    f.write(f"# co_names: {code.co_names}\n")
                    f.write(f"# co_varnames: {code.co_varnames}\n")
                    f.write(f"# co_consts: {code.co_consts}\n")
                except Exception as e:
                    f.write(f"# Error extracting code object: {str(e)}\n")
        
        print(f"Basic disassembly saved to '{output_file}'. Manual reconstruction may be needed.")
        return True
    except Exception as e:
        print(f"All decompilation methods failed: {str(e)}")
        return False
def generate_pseudo_code(pyc_file, pseudo_file, py_version):
    try:
        print(f"Generating pseudo-code file: {pseudo_file}")
        with open(pseudo_file, 'w') as f:
            f.write(f"# Pseudo-code analysis for: {os.path.basename(pyc_file)}\n")
            f.write(f"# Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            if py_version:
                f.write(f"# Detected Python version: {py_version[0]}.{py_version[1]}\n")
            f.write("#" + "=" * 70 + "\n\n")
            
            with open(pyc_file, 'rb') as pyc:
                pyc.seek(16)
                try:
                    code = marshal.load(pyc)
                    f.write("# CODE OBJECT ANALYSIS\n")
                    f.write("#" + "-" * 70 + "\n\n")
                    
                    f.write("# Basic Information:\n")
                    f.write(f"#   Filename: {code.co_filename}\n")
                    f.write(f"#   Name: {code.co_name}\n")
                    f.write(f"#   First line: {code.co_firstlineno}\n")
                    f.write(f"#   Argument count: {code.co_argcount}\n")
                    if hasattr(code, 'co_posonlyargcount'):
                        f.write(f"#   Positional-only argument count: {code.co_posonlyargcount}\n")
                    if hasattr(code, 'co_kwonlyargcount'):
                        f.write(f"#   Keyword-only argument count: {code.co_kwonlyargcount}\n")
                    f.write("\n")
                    
                    f.write("# Names (globals, functions, classes, etc.):\n")
                    for i, name in enumerate(code.co_names):
                        f.write(f"#   {i}: {name}\n")
                    f.write("\n")
                    
                    f.write("# Variable names (locals):\n")
                    for i, var in enumerate(code.co_varnames):
                        f.write(f"#   {i}: {var}\n")
                    f.write("\n")
                    
                    f.write("# Constants:\n")
                    for i, const in enumerate(code.co_consts):
                        if isinstance(const, type(code)):
                            f.write(f"#   {i}: <code object> name={const.co_name} argcount={const.co_argcount}\n")
                        else:
                            const_str = str(const)
                            if len(const_str) > 50:
                                const_str = const_str[:47] + "..."
                            f.write(f"#   {i}: {const_str} (type: {type(const).__name__})\n")
                    f.write("\n")
                    
                    f.write("# BYTECODE ANALYSIS\n")
                    f.write("#" + "-" * 70 + "\n\n")
                    
                    f.write("# Disassembly:\n")
                    old_stdout = sys.stdout
                    sys.stdout = f
                    dis.dis(code)
                    sys.stdout = old_stdout
                    f.write("\n")
                    
                    f.write("# CONTROL FLOW ANALYSIS\n")
                    f.write("#" + "-" * 70 + "\n\n")
                    
                    try:
                        jump_targets = set()
                        instructions = list(dis.get_instructions(code))
                        
                        for instr in instructions:
                            if 'JUMP' in instr.opname or instr.opname.startswith('FOR_ITER') or instr.opname in ('SETUP_FINALLY', 'SETUP_EXCEPT', 'SETUP_WITH'):
                                if instr.argval is not None:
                                    jump_targets.add(instr.argval)
                        
                        f.write("# Jump targets and control structures:\n")
                        for instr in instructions:
                            if instr.offset in jump_targets:
                                f.write(f"# {'>' * 10} JUMP TARGET {'<' * 10}\n")
                            
                            f.write(f"#  {instr.offset}: {instr.opname} {instr.arg if instr.arg is not None else ''}\n")
                            
                            if 'JUMP' in instr.opname:
                                if 'IF' in instr.opname or 'POP' in instr.opname:
                                    f.write(f"#    ↳ Conditional jump to {instr.argval}\n")
                                else:
                                    f.write(f"#    ↳ Unconditional jump to {instr.argval}\n")
                            elif instr.opname == 'FOR_ITER':
                                f.write(f"#    ↳ Loop iteration, jump to {instr.argval} when exhausted\n")
                            elif instr.opname in ('SETUP_FINALLY', 'SETUP_EXCEPT', 'SETUP_WITH'):
                                f.write(f"#    ↳ Exception/context handler setup, jump to {instr.argval}\n")
                            elif 'CALL' in instr.opname:
                                f.write(f"#    ↳ Function call\n")
                            elif 'LOAD_GLOBAL' in instr.opname:
                                f.write(f"#    ↳ Global reference: {instr.argval}\n")
                            elif 'LOAD_ATTR' in instr.opname:
                                f.write(f"#    ↳ Attribute access: {instr.argval}\n")
                    except Exception as flow_err:
                        f.write(f"# Error in control flow analysis: {str(flow_err)}\n")
                    
                    f.write("\n")
                    
                    f.write("# SIMPLIFIED STACK ANALYSIS\n")
                    f.write("#" + "-" * 70 + "\n\n")
                    f.write("# Note: This is a simplified model of stack operations\n")
                    
                    try:
                        instructions = list(dis.get_instructions(code))
                        stack_effects = []
                        
                        for instr in instructions:
                            try:
                                if hasattr(dis, 'stack_effect'):
                                    effect = dis.stack_effect(instr.opcode, instr.arg if instr.arg is not None else 0)
                                    stack_effects.append((instr.offset, instr.opname, effect))
                            except:
                                pass
                        
                        if stack_effects:
                            f.write("# Instruction stack effects:\n")
                            for offset, opname, effect in stack_effects:
                                symbol = "+" if effect > 0 else "" if effect == 0 else "-"
                                f.write(f"#  {offset}: {opname} (stack effect: {symbol}{abs(effect)})\n")
                        
                    except Exception as stack_err:
                        f.write(f"# Error in stack analysis: {str(stack_err)}\n")
                    
                    f.write("\n# INFERRED CODE STRUCTURE\n")
                    f.write("#" + "-" * 70 + "\n\n")
                    
                    def_patterns = [name for name in code.co_names if name.startswith('def_')]
                    class_patterns = [name for name in code.co_names if name[0].isupper() and len(name) > 1]
                    import_patterns = [name for name in code.co_names 
                                     if name in ['import', 'from'] or 
                                     name in ['os', 'sys', 'time', 're', 'math', 'socket', 'threading']]
                    
                    functions = set()
                    for name in code.co_names:
                        if name[0].islower() and len(name) > 1 and not name.startswith('__'):
                            for instr in instructions:
                                if instr.opname == 'LOAD_GLOBAL' and instr.argval == name:
                                    next_idx = instructions.index(instr) + 1
                                    if next_idx < len(instructions) and 'CALL' in instructions[next_idx].opname:
                                        functions.add(name)
                                        break
                    
                    if import_patterns:
                        f.write("# Possible imports:\n")
                        for name in import_patterns:
                            f.write(f"import {name}\n")
                        f.write("\n")
                    
                    if class_patterns:
                        f.write("# Possible classes:\n")
                        for name in class_patterns:
                            f.write(f"class {name}:\n    # methods and attributes\n    pass\n\n")
                    
                    if functions:
                        f.write("# Possible functions:\n")
                        for name in functions:
                            f.write(f"def {name}(...):\n    # implementation\n    pass\n\n")
                    
                    if "__name__" in code.co_names and "__main__" in [str(c) for c in code.co_consts if isinstance(c, str)]:
                        f.write("# Main execution block detected:\n")
                        f.write("if __name__ == \"__main__\":\n    # main code\n    pass\n")
                    
                    f.write("\n# NESTED CODE OBJECTS\n")
                    f.write("#" + "-" * 70 + "\n\n")
                    
                    nested_codes = [const for const in code.co_consts if isinstance(const, type(code))]
                    if nested_codes:
                        for i, nested_code in enumerate(nested_codes):
                            f.write(f"# Nested code object #{i+1}: {nested_code.co_name}\n")
                            f.write(f"#   Type: {'Class method' if nested_code.co_name.startswith('__') else 'Function'}\n")
                            f.write(f"#   Arguments: {nested_code.co_argcount}\n")
                            f.write(f"#   Local variables: {', '.join(nested_code.co_varnames[:10])}{' (truncated)' if len(nested_code.co_varnames) > 10 else ''}\n")
                            f.write("\n#   Disassembly:\n")
                            
                            old_stdout = sys.stdout
                            sys.stdout = f
                            dis.dis(nested_code)
                            sys.stdout = old_stdout
                            f.write("\n")
                    else:
                        f.write("# No nested code objects found\n")
                    
                except Exception as e:
                    f.write(f"# Error analyzing code object: {str(e)}\n")
                    traceback_str = traceback.format_exc()
                    f.write(f"# Traceback: {traceback_str}\n")
                    
                    try:
                        f.write("\n# FALLBACK: RAW BYTECODE ANALYSIS\n")
                        f.write("#" + "-" * 70 + "\n\n")
                        
                        pyc.seek(0)
                        raw_data = pyc.read()
                        
                        f.write(f"# Magic bytes: {raw_data[:4].hex()}\n")
                        
                        ascii_strings = re.findall(b'[\x20-\x7E]{4,}', raw_data)
                        if ascii_strings:
                            f.write("\n# ASCII strings found in bytecode:\n")
                            for i, s in enumerate(ascii_strings[:100]):
                                try:
                                    decoded = s.decode('utf-8')
                                    if re.match(r'^[a-zA-Z0-9_.\-+*/\\:;,<>?!@#$%^&*()[\]{}]+$', decoded):
                                        f.write(f"#   {decoded}\n")
                                except:
                                    pass
                            
                            if len(ascii_strings) > 100:
                                f.write(f"#   ... and {len(ascii_strings) - 100} more strings\n")
                    except Exception as raw_err:
                        f.write(f"# Error in raw bytecode analysis: {str(raw_err)}\n")
            
            f.write("\n#" + "=" * 70 + "\n")
            f.write("# End of pseudo-code analysis\n")
        
        print(f"Successfully generated pseudo-code file: {pseudo_file}")
        return True
    except Exception as e:
        print(f"Error generating pseudo-code: {str(e)}")
        traceback.print_exc()
        return False
    
def process_directory(directory, recursive=False, force=False, use_alternative=False, create_pseudo=False):
    success = 0
    failed = 0
    
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.pyc'):
                pyc_path = os.path.join(root, file)
                if pyc_to_py(pyc_path, force=force, use_alternative=use_alternative, create_pseudo=create_pseudo):
                    success += 1
                else:
                    failed += 1
        
        if not recursive:
            break
    
    return success, failed

def main():
    parser = argparse.ArgumentParser(description='Convert .pyc files to .py files.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-f', '--file', help='Path to the .pyc file to convert')
    group.add_argument('-d', '--directory', help='Directory containing .pyc files to convert')
    
    parser.add_argument('-o', '--output', help='Output file path (only used with --file)')
    parser.add_argument('-r', '--recursive', action='store_true', help='Recursively process subdirectories')
    parser.add_argument('--force', action='store_true', help='Overwrite existing .py files')
    parser.add_argument('--alt', '--alternative', action='store_true', dest='alternative',
                      help='Try alternative decompilers if the primary one fails')
    parser.add_argument('-v', '--verbose', action='store_true', help='Show detailed error information')
    parser.add_argument('--py312', action='store_true', help='Force Python 3.12 specific handling')
    parser.add_argument('--py313', action='store_true', help='Force Python 3.13 specific handling')
    parser.add_argument('-p', '--pseudo', action='store_true', help='Generate a pseudo-code file alongside the decompiled output')
    
    args = parser.parse_args()
    
    if args.verbose:
        print("Verbose mode enabled - will show detailed error information")
    else:
        sys.tracebacklimit = 0
    
    if args.file:
        if args.py312 or args.py313:
            output = args.output
            if output is None:
                if args.file.endswith('.pyc'):
                    output = args.file[:-4] + '.py'
                else:
                    output = args.file + '.py'
            
            py_version = (3, 13) if args.py313 else (3, 12)
            handle_modern_python_pyc(args.file, output, py_version)
        else:
            pyc_to_py(args.file, args.output, args.force, args.alternative, args.pseudo)
    elif args.directory:
        success, failed = process_directory(args.directory, args.recursive, args.force, args.alternative, args.pseudo)
        print(f"Conversion complete: {success} successful, {failed} failed")

if __name__ == '__main__':
    main()