import inspect
import logging
from string import Template as strfmt

from mako.template import Template

from ...metamodel import arch, behav
from . import instruction_transform, instruction_utils
from .templates import template_dir


logger = logging.getLogger("instruction_generator")

def patch_model():
	"""Monkey patch transformation functions inside instruction_transform
	into model_classes.behav classes
	"""

	for name, fn in inspect.getmembers(instruction_transform, inspect.isfunction):
		sig = inspect.signature(fn)
		param = sig.parameters.get("self")
		if not param:
			logger.warning("no self parameter found in %s", fn)
			continue
		if not param.annotation:
			logger.warning("self parameter not annotated correctly for %s", fn)
			continue

		logger.debug("patching %s with fn %s", param.annotation, fn)
		param.annotation.generate = fn

patch_model()

def generate_functions(core: arch.CoreDef):
	"""Return a generator object to generate function behavior code. Uses function
	definitions in the core object.
	"""

	fn_template = Template(filename=str(template_dir/'etiss_function.mako'))

	core_default_width = core.constants['XLEN'].value
	core_name = core.name

	for fn_name, fn_def in core.functions.items():
		logger.debug("setting up function generator for %s", fn_name)

		return_type = instruction_utils.data_type_map[fn_def.data_type]
		if fn_def.size:
			return_type += f'{fn_def.actual_size}'

		context = instruction_utils.TransformerContext(core.constants, core.memories, core.memory_aliases, fn_def.args, [], core.functions, 0, core_default_width, core_name, True)

		logger.debug("generating code for %s", fn_name)

		out_code = fn_def.operation.generate(context)
		out_code = strfmt(out_code).safe_substitute(ARCH_NAME=core_name)

		fn_def.static = not context.used_arch_data

		logger.debug("generating header for %s", fn_name)

		args_list = [f'{instruction_utils.data_type_map[arg.data_type]}{arg.actual_size} {arg.name}' for arg in fn_def.args.values()]
		if not fn_def.static:
			args_list = ['ETISS_CPU * const cpu', 'ETISS_System * const system', 'void * const * const plugin_pointers'] + args_list

		fn_args = ', '.join(args_list)

		logger.debug("rendering template for %s", fn_name)

		templ_str = fn_template.render(
			return_type=return_type,
			fn_name=fn_name,
			args_list=fn_args,
			static=fn_def.static,
			operation=out_code
		)

		yield (fn_name, templ_str)

def generate_fields(core_default_width, instr_def: arch.Instruction):
	"""Generate the extraction code for all fields of an instr_def"""

	enc_idx = 0

	seen_fields = {}

	fields_code = ""
	asm_printer_code = []

	logger.debug("generating instruction parameters for %s", instr_def.name)

	for enc in reversed(instr_def.encoding):
		if isinstance(enc, arch.BitField):
			logger.debug("adding parameter %s", enc.name)
			if enc.name not in seen_fields:
				seen_fields[enc.name] = 255
				fields_code += f'{instruction_utils.data_type_map[enc.data_type]}{core_default_width} {enc.name} = 0;\n'

			lower = enc.range.lower
			upper = enc.range.upper
			length = enc.range.length

			if seen_fields[enc.name] > lower:
				seen_fields[enc.name] = lower

			fields_code += f'static BitArrayRange R_{enc.name}_{lower}({enc_idx+length-1}, {enc_idx});\n'
			fields_code += f'{enc.name} += R_{enc.name}_{lower}.read(ba) << {lower};\n'

			if instr_def.fields[enc.name].upper < upper:
				instr_def.fields[enc.name].upper = upper

			enc_idx += length
		else:
			logger.debug("adding fixed encoding part")
			enc_idx += enc.length

	logger.debug("generating asm_printer and sign extensions")
	for field_name, field_descr in reversed(instr_def.fields.items()):
		# generate asm_printer code
		asm_printer_code.append(f'{field_name}=" + std::to_string({field_name}) + "')

		# generate sign extension if necessary
		if field_descr.data_type == arch.DataType.S and field_descr.upper + 1 < core_default_width:
			fields_code += '\n'
			fields_code += f'struct {{etiss_int{core_default_width} x:{field_descr.upper+1};}} {field_name}_ext;\n'
			fields_code += f'{field_name} = {field_name}_ext.x = {field_name};'

	asm_printer_code = f'ss << "{instr_def.name.lower()}" << " # " << ba << (" [' + ' | '.join(reversed(asm_printer_code)) + ']");'

	return (fields_code, asm_printer_code, seen_fields, enc_idx)

def generate_instructions(core: arch.CoreDef):
	"""Return a generator object to generate instruction behavior code. Uses instruction
	definitions in the core object.
	"""

	instr_template = Template(filename=str(template_dir/'etiss_instruction.mako'))

	temp_var_count = 0
	mem_var_count = 0

	core_default_width = core.constants['XLEN'].value
	core_name = core.name

	for (code, mask), instr_def in core.instructions.items():
		logger.debug("setting up instruction generator for %s", instr_def.name)

		instr_name = instr_def.name
		misc_code = []

		if instr_def.attributes == None:
			instr_def.attributes = []

		fields_code, asm_printer_code, seen_fields, enc_idx = generate_fields(core.constants['XLEN'].value, instr_def)

		context = instruction_utils.TransformerContext(core.constants, core.memories, core.memory_aliases, instr_def.fields, instr_def.attributes, core.functions, enc_idx, core_default_width, core_name)

		# add pc increment to operation tree
		if arch.InstrAttribute.NO_CONT not in instr_def.attributes:
			logger.debug("appending PC increment to operation tree")
			instr_def.operation.statements.append(
				behav.Assignment(
					behav.NamedReference(context.pc_mem),
					behav.BinaryOperation(
						behav.NamedReference(context.pc_mem),
						behav.Operator("+"),
						behav.NumberLiteral(int(enc_idx/8))
					)
				)
			)

		if arch.InstrAttribute.NO_CONT in instr_def.attributes and arch.InstrAttribute.COND not in instr_def.attributes:
			logger.debug("adding forced block end")
			misc_code.append('ic.force_block_end_ = true;')

		code_string = f'{code:#0{int(enc_idx/4)}x}'
		mask_string = f'{mask:#0{int(enc_idx/4)}x}'

		# generate instruction behavior code
		logger.debug("generating behavior code for %s", instr_def.name)

		out_code = instr_def.operation.generate(context)
		out_code = strfmt(out_code).safe_substitute(ARCH_NAME=core_name)

		if context.temp_var_count > temp_var_count:
			temp_var_count = context.temp_var_count

		if context.mem_var_count > mem_var_count:
			mem_var_count = context.mem_var_count

		logger.debug("rendering template for %s", instr_def.name)
		# render code for whole instruction
		templ_str = instr_template.render(
			instr_name=instr_name,
			seen_fields=seen_fields,
			enc_idx=enc_idx,
			core_name=core_name,
			code_string=code_string,
			mask_string=mask_string,
			misc_code=misc_code,
			fields_code=fields_code,
			asm_printer_code=asm_printer_code,
			core_default_width=core_default_width,
			reg_dependencies=context.dependent_regs,
			reg_affected=context.affected_regs,
			operation=out_code
		)

		yield (instr_name, (code, mask), instr_def.ext_name, templ_str)
