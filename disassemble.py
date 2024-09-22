from instruction_data import (
    GLOBAL_INSTRUCTIONS_MAP,
    GLOBAL_REGISTER_NAMES,
    REGADD_OPCODES,
    CALL_GENERATING,
    InstructionInfo,
    Encodings,
)
from byte_utils import parse_modrm, parse_sib, get_file, to_signed


# simple data class for building up a disassembled instruction with string tokens
class Instruction:
    def __init__(
        self,
        mnemonic=None,
        encoding=None,
        immediate=None,
        reg=None,
        rm=None,
        scale=None,
        index=None,
        base=None,
        is_db=False,
    ):
        self.mnemonic = mnemonic
        self.encoding = encoding
        self.immediate = immediate
        self.reg = reg
        self.rm = rm
        self.scale = scale
        self.index = index
        self.base = base
        self.is_db = is_db

    def __str__(self) -> str:

        # handle db
        if self.is_db:
            return f"db 0x{self.immediate:02X}"

        # print instructions based on encoding
        if self.encoding == Encodings.M:
            return f"{self.mnemonic} {self.rm}"
        elif self.encoding == Encodings.MI:
            return f"{self.mnemonic} {self.rm}, {self.immediate}"
        elif self.encoding == Encodings.MR:
            return f"{self.mnemonic} {self.rm}, {self.reg}"
        elif self.encoding == Encodings.RM:
            return f"{self.mnemonic} {self.reg}, {self.rm}"
        elif self.encoding == Encodings.I:
            return f"{self.mnemonic} {self.immediate}"
        elif self.encoding == Encodings.O:
            return f"{self.mnemonic} {self.reg}"
        elif self.encoding == Encodings.OI:
            return f"{self.mnemonic} {self.reg}, {self.immediate}"
        elif self.encoding == Encodings.FD:
            return f"{self.mnemonic} {self.reg}, {self.immediate}"
        elif self.encoding == Encodings.TD:
            return f"{self.mnemonic} {self.immediate}, {self.reg}"
        elif self.encoding == Encodings.D:
            return f"{self.mnemonic} {self.immediate}"

        else:  # ZO encoding
            return self.mnemonic


# get the mnemonic of a modrm instruction, handling opcode extension if needed
def modrm_get_mnemonic(reg: int, instruction_info: InstructionInfo) -> str:

    # check opcode extension if it exists to get the mnemonic based on reg
    if instruction_info.extension_map and reg in instruction_info.extension_map:
        return instruction_info.extension_map[reg]
    # check if there is an illegal opcode extension
    elif instruction_info.extension_map:
        raise Exception(
            f"INVALID OPCODE EXTENSION {reg} FOR OPCODE {instruction_info.opcode:X}"
        )
    # return mnemonic directly from the instruction info
    else:
        return instruction_info.mnemonic


def modrm_get_addressing_mode(mod: int, instruction_info: InstructionInfo) -> int:
    if mod in instruction_info.addressing_modes:
        return mod
    else:
        raise Exception(
            f"INVALID ADDRESSING MODE {mod} FOR OPCODE {instruction_info.opcode:X}"
        )


# disassemble instruction with modrm
def modrm_disassemble(data: bytearray, opcode_size, instruction_info: InstructionInfo):
    # account for opcode size + modrm
    instruction_size = opcode_size + 1

    # parse the modrm byte
    (mod, reg, rm) = parse_modrm(data[0])

    # start building the instruction object
    instruction = Instruction(
        mnemonic=modrm_get_mnemonic(reg, instruction_info),
        encoding=instruction_info.encoding,
        reg=GLOBAL_REGISTER_NAMES[reg],
    )

    # safely get addressing mode
    mod = modrm_get_addressing_mode(mod, instruction_info)

    # continue building instruction based on addressing mode:

    # r/m is a direct register
    if mod == 3:
        instruction.rm = GLOBAL_REGISTER_NAMES[rm]

    # r/m is register + dword displacement
    elif mod == 2:
        # sib byte detected
        if rm == 4:
            instruction_size += 5
            displacement = int.from_bytes(data[2:6], "little", signed=False)
            (scale, index, base) = parse_sib(data[1])
            # handle ESP
            if index == 4:
                instruction.rm = f"[ dword {GLOBAL_REGISTER_NAMES[base]}"
            else:
                instruction.rm = f"[ dword {GLOBAL_REGISTER_NAMES[index]}*{scale} + {GLOBAL_REGISTER_NAMES[base]}"
        else:
            instruction_size += 4
            displacement = int.from_bytes(data[1:5], "little", signed=False)
            instruction.rm = f"[ dword {GLOBAL_REGISTER_NAMES[rm]}"

        if not displacement == 0:
            instruction.rm += f" + 0x{displacement:08X}"

        instruction.rm += " ]"

    # rm is register + byte displacement
    elif mod == 1:
        # sib byte detected
        if rm == 4:
            instruction_size += 2
            displacement = to_signed(data[2])
            (scale, index, base) = parse_sib(data[1])
            # handle ESP
            if index == 4:
                instruction.rm = f"[ byte {GLOBAL_REGISTER_NAMES[base]}"
            else:
                instruction.rm = f"[ byte {GLOBAL_REGISTER_NAMES[index]}*{scale} + {GLOBAL_REGISTER_NAMES[base]}"
        else:
            instruction_size += 1
            displacement = to_signed(data[1])
            instruction.rm = f"[ byte {GLOBAL_REGISTER_NAMES[rm]}"

        if not displacement == 0:
            instruction.rm += (
                f" {"+" if displacement > 0 else "-"} 0x{abs(displacement):02X}"
            )

        instruction.rm += " ]"

    # mod is 0, check special cases
    else:
        # r/m is a displacement32
        if rm == 5:
            instruction_size += 4
            displacement = int.from_bytes(data[1:5], "little", signed=False)
            instruction.rm = f"[ 0x{displacement:08X} ]"

        # sib byte
        elif rm == 4:
            instruction_size += 1
            (scale, index, base) = parse_sib(data[1])
            # check for special cases of SIB byte at this spexifix mod
            # handle ESP
            if index == 4:
                instruction.rm = f"[ {GLOBAL_REGISTER_NAMES[base]} ]"
            elif base == 5:
                instruction.rm = f"[ {GLOBAL_REGISTER_NAMES[index]}*{scale}"
                instruction_size += 4
                displacement = int.from_bytes(data[2:6], "little", signed=False)
                if not displacement == 0:
                    instruction.rm += f" + 0x{displacement:08X}"

                instruction.rm += " ]"

            else:
                instruction.rm = f"[ {GLOBAL_REGISTER_NAMES[index]}*{scale} + {GLOBAL_REGISTER_NAMES[base]} ]"

        # register only
        else:
            instruction.rm = f"[ {GLOBAL_REGISTER_NAMES[rm]} ]"

    # handle an immediate in the case of an MI instruction
    if instruction_info.encoding == Encodings.MI:

        instruction.immediate = int.from_bytes(
            data[
                instruction_size - 1 : instruction_size - 1 + instruction_info.imm_size
            ],
            "little",
            signed=False,
        )
        if instruction_info.imm_size == 4:
            instruction.immediate = f"0x{instruction.immediate:08X}"

        instruction_size += instruction_info.imm_size

    return instruction, instruction_size


# disassemble instruction with no modrm byte or o/oi encoding
def no_modrm_no_regadd_disassemble(
    data, opcode_size, instruction_info: InstructionInfo, offset
):
    # handle based on  encoding type

    instruction_size = opcode_size
    instruction = Instruction(
        mnemonic=instruction_info.mnemonic, encoding=instruction_info.encoding
    )

    # TODO: handle endianness and negative offsets/immediates

    # we don't have to do anything in the cae of ZO instructions
    if not instruction.encoding == Encodings.ZO:

        # check for the exclusive EAX instructions
        if (
            instruction_info.encoding == Encodings.TD
            or instruction_info.encoding == Encodings.FD
        ):
            instruction.reg = "eax"

        # get the immediate size
        instruction_size += instruction_info.imm_size

        # set the immediate based on the size
        if instruction.encoding == Encodings.I:
            if instruction_info.imm_size == 4:

                imm = instruction.immediate = int.from_bytes(
                    data[: instruction_info.imm_size], "little"
                )
                instruction.immediate = f"0x{imm:08X}"

            elif instruction_info.imm_size == 2:

                imm = instruction.immediate = int.from_bytes(
                    data[: instruction_info.imm_size], "little"
                )
                instruction.immediate = f"0x{imm:04X}"
            else:
                imm = instruction.immediate = int.from_bytes(
                    data[: instruction_info.imm_size], "little", signed=True
                )
                instruction.immediate = f"{imm}"

        # encoding is D, indicating a relative offset
        else:
            if instruction_info.imm_size == 4:

                imm = instruction.immediate = int.from_bytes(
                    data[: instruction_info.imm_size], "little", signed=True
                )

            else:
                imm = instruction.immediate = int.from_bytes(
                    data[: instruction_info.imm_size], "little", signed=True
                )

            instruction.immediate += offset+instruction_size

    return instruction, instruction_size


# check if an opcode cooresponds to one of the O/OI opcodes with something added
def regadd_check_opcode(opcode):

    for regadd_opcode in REGADD_OPCODES:
        if (opcode - regadd_opcode >= 0) and (opcode - regadd_opcode < 8):
            return GLOBAL_INSTRUCTIONS_MAP[regadd_opcode]
    return None


def regadd_disassemble(data, instruction_info: InstructionInfo):
    # handle based on  encoding type
    instruction_size = 1
    instruction = Instruction(
        mnemonic=instruction_info.mnemonic,
        encoding=instruction_info.encoding,
        reg=GLOBAL_REGISTER_NAMES[data[0] - instruction_info.opcode],
    )

    # check for immediate
    if instruction_info.encoding == Encodings.OI:
        instruction_size += instruction_info.imm_size
        imm = int.from_bytes(data[1:instruction_size], byteorder="little", signed=False)
        instruction.immediate = f"0x{imm:08X}"

    return instruction, instruction_size


def disassemble(data, offset):
    # figure out the opcode and get a respective instruction data object

    # check for single byte opcode
    if data[0] in GLOBAL_INSTRUCTIONS_MAP:
        opcode_size = 1
        instruction_info = GLOBAL_INSTRUCTIONS_MAP[data[0]]

    # check for two byte opcode
    elif int.from_bytes(data[:2], "big") in GLOBAL_INSTRUCTIONS_MAP:
        opcode_size = 2
        instruction_info = GLOBAL_INSTRUCTIONS_MAP[int.from_bytes(data[:2], "big")]

    # check for opcode math
    else:
        instruction_info = regadd_check_opcode(data[0])
        # unknown instruction, return a db
        if not instruction_info:
            return Instruction(immediate=data[0], is_db=True), 1

    # decode the actual instruction based on the above info

    try:
        # handle modrm instruction
        if instruction_info.has_modrm:
            instruction, instruction_size = modrm_disassemble(
                data[opcode_size:], opcode_size, instruction_info
            )
        # handle o/oi instructions
        elif (
            instruction_info.encoding == Encodings.O
            or instruction_info.encoding == Encodings.OI
        ):
            instruction, instruction_size = regadd_disassemble(data, instruction_info)
        # handle all other instruction types
        else:
            instruction, instruction_size = no_modrm_no_regadd_disassemble(
                data[opcode_size:], opcode_size, instruction_info, offset
            )

        return instruction, instruction_size

    except Exception as e:
        return Instruction(immediate=data[0], is_db=True), 1

# linnear sweep algorithm for disassembly
def linnear_sweep(filename: str):
    counter = 0
    output_list = {}
    labels = {}

    # get the binary data from the file
    data = get_file(filename)

    while counter < len(data):
        original_offset = counter

        # TODO: HANDLE FUNCTION CALLS / JUMPS

        # disassemble the instruction and get the instruction size
        instruction, instruction_size = disassemble(data[counter:], original_offset)

        if instruction.encoding == Encodings.D:
            # do something to handle calls and labels
            dest_addr_str = f"{instruction.immediate:08X}"
            dest_label = f"offset_{dest_addr_str}h"
            labels[instruction.immediate]=dest_label
            instruction.immediate = dest_label


        # store the instruction in the output list along with the raw bytes
        output_list[original_offset] = (
            instruction,
            data[original_offset : original_offset + instruction_size],
        )

        counter += instruction_size

    return output_list, labels
