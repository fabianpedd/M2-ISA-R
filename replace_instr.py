import pickle
import re

repl_re = r'''InstructionDefinition (?P<instr_def_name>\S*)\s*\(
\s*(?P<instr_arch>\S*),
\s*"(?P<instr_name>\S*)",
\s*\(uint\d\d?_t\)\s*(?P<code>\S*),
\s*\(uint\d\d?_t\)\s*(?P<mask>\S*),
(?P<operation>.*?),
0,
(?P<asm_printer>.*?)
\);
'''

with open('/home/wysiwyng/work/coredsl/coredsl-models/riscv/gen_output/RISCV.pickle', 'rb') as f:
	functions = pickle.load(f)
	instrs = pickle.load(f)

new_opcodes = sorted(list(instrs["RISCV"].keys()))

with open('/home/wysiwyng/work/etiss-mine/ArchImpl/RISCV/RISCVArch.cpp', 'r+') as f:
	text = f.read()

matches = list(re.finditer(repl_re, text, re.DOTALL))
old_opcodes = sorted([(int(m.group('code'), 16), int(m.group('mask'), 16)) for m in matches])

not_in_new = set(old_opcodes).difference(new_opcodes)
not_in_old = set(new_opcodes).difference(old_opcodes)

pass
