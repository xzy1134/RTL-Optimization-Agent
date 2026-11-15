"""Conservative connectivity hints from an elaborated Yosys generic netlist."""
from collections import Counter
from dataclasses import dataclass, field

ARITH = {'$add', '$sub', '$mul', '$div', '$mod', '$divfloor', '$modfloor'}
BOOLEAN = {'$and', '$or', '$xor', '$xnor', '$logic_and', '$logic_or', '$eq', '$ne', '$lt', '$le', '$gt', '$ge', '$not', '$logic_not', '$reduce_and', '$reduce_or'}


@dataclass
class Evidence:
    kind: str
    cells: list[str]
    details: dict
    confidence: str
    unconfirmed: list[str]


@dataclass
class RTLFeatures:
    top: str
    status: str = 'OK'
    message: str = ''
    run_dir: str = ''
    source_hashes: list[str] = field(default_factory=list)
    cell_counts: dict = field(default_factory=dict)
    ports: dict = field(default_factory=dict)
    sequential_cells: list[str] = field(default_factory=list)
    unsupported_cells: list[str] = field(default_factory=list)
    proof_limitations: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)


def analyze_netlist(data, top):
    from rtl_agent.eda.verifier import COMB_CELLS
    module = data['modules'][top]
    cells = module.get('cells', {})
    result = RTLFeatures(top, cell_counts=dict(sorted(Counter(c['type'] for c in cells.values()).items())),
                         ports={n: {'direction': p['direction'], 'width': len(p['bits']),
                                    'signed': p.get('signed', 0)} for n, p in sorted(module['ports'].items())})
    if module.get('memories'):
        result.proof_limitations.append('Memories are outside the M1 proof contract')
    all_bits = [bits for c in cells.values() for bits in c['connections'].values()]
    all_bits += [p['bits'] for p in module['ports'].values()]
    if any(bit in ('x', 'z') for bits in all_bits for bit in bits):
        result.proof_limitations.append('Explicit undefined/high-impedance connections are outside the M1 proof contract')
    if not any(p['direction'] == 'output' for p in module['ports'].values()):
        result.proof_limitations.append('No output ports to prove')
    outputs = {}
    consumers = Counter()
    for p in module['ports'].values():
        if p['direction'] in ('output', 'inout'):
            consumers.update(b for b in p['bits'] if isinstance(b, int))
    for name, c in sorted(cells.items()):
        kind = c['type']
        if kind.startswith(('$dff', '$adff', '$sdff', '$aldff', '$dlatch', '$adlatch', '$ff', '$sr')):
            result.sequential_cells.append(name)
        if kind not in COMB_CELLS:
            result.unsupported_cells.append(name)
        for port, bits in c['connections'].items():
            direction = c.get('port_directions', {}).get(port)
            if direction == 'output':
                outputs.setdefault(tuple(bits), []).append(name)
            elif direction in ('input', 'inout'):
                consumers.update(b for b in bits if isinstance(b, int))

    for name, c in sorted(cells.items()):
        kind, con = c['type'], c['connections']
        if kind == '$mux':
            a, b = outputs.get(tuple(con['A']), []), outputs.get(tuple(con['B']), [])
            if len(a) == len(b) == 1 and a[0] != b[0]:
                ca, cb = cells[a[0]], cells[b[0]]
                keys = ('A_WIDTH', 'B_WIDTH', 'Y_WIDTH', 'A_SIGNED', 'B_SIGNED')
                if ca['type'] == cb['type'] and ca['type'] in ARITH and all(ca.get('parameters', {}).get(k) == cb.get('parameters', {}).get(k) for k in keys):
                    extra = any(consumers[bit] > 1 for bits in (con['A'], con['B']) for bit in bits if isinstance(bit, int))
                    same = all(ca['connections'].get(p) == cb['connections'].get(p) for p in ('A', 'B'))
                    result.evidence.append(Evidence('shared_operator_mux', [a[0], b[0], name],
                        {'operator': ca['type'], 'extra_consumers': extra, 'identical_operands': same,
                         'width': len(con['Y'])}, 'medium' if extra or same else 'high',
                        ['Check other consumers and operand semantics', 'Measure input-mux depth and mapped area',
                         'Synthesis may already share identical computations']))
        if kind in ARITH:
            y = con.get('Y', [])
            used = [i for i, bit in enumerate(y) if consumers[bit] > 0]
            if used and max(used) + 1 < len(y):
                result.evidence.append(Evidence('unused_high_output_bits', [name],
                    {'output_width': len(y), 'highest_used_bit': max(used)}, 'high',
                    ['Prove carry, signedness and truncation semantics before reducing internal widths']))
            for port in ('A', 'B'):
                bits = con.get(port, [])
                if len(bits) > 1 and (bits[-1] == '0' or bits[-1] == bits[-2]):
                    result.evidence.append(Evidence('operand_extension_hint', [name],
                        {'port': port, 'width': len(bits)}, 'low',
                        ['Repeated or zero high bits do not prove a safe reduced arithmetic width']))
            predecessors = sorted({p for port in ('A', 'B') for p in outputs.get(tuple(con.get(port, [])), []) if cells[p]['type'] in ARITH})
            if predecessors:
                result.evidence.append(Evidence('arithmetic_chain', predecessors + [name],
                    {'operator': kind}, 'medium', ['Check intermediate widths and associativity in finite-width arithmetic']))
            if kind == '$mul' and any(all(isinstance(b, str) and b in '01' for b in con.get(p, [])) and con.get(p) for p in ('A', 'B')):
                result.evidence.append(Evidence('constant_multiply', [name], {}, 'medium',
                    ['ABC may already implement constant multiplication efficiently']))
    groups = [('mux_presence', {'$mux', '$pmux', '$bmux', '$demux'}, 'low'),
              ('boolean_presence', BOOLEAN, 'low'), ('arithmetic_presence', ARITH, 'low'),
              ('variable_shift', {'$shl', '$shr', '$sshl', '$sshr', '$shift', '$shiftx'}, 'low')]
    for hint, types, confidence in groups:
        names = [n for n, c in sorted(cells.items()) if c['type'] in types and
                 (hint != 'variable_shift' or any(isinstance(b, int) for b in c['connections'].get('B', [])))]
        if names:
            result.evidence.append(Evidence(hint, names, {}, confidence,
                ['Presence alone does not establish redundancy, applicability or area savings']))
    if result.sequential_cells:
        result.evidence.append(Evidence('state_logic_hint', result.sequential_cells, {}, 'low',
            ['Registers do not establish the presence of an FSM', 'Sequential equivalence backend is required']))
    return result
