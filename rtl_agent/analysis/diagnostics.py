"""Read-only diagnostics for M1 artifacts. A diagnostic never scores a candidate."""
from collections import defaultdict
from graphlib import TopologicalSorter, CycleError
import json
from pathlib import Path


def load(directory, name):
    return json.loads((Path(directory) / name).read_text())


def paths(directory):
    """Count one level per cell input-bit to output-bit arc, as in Yosys ltp."""
    try:
        result = load(directory, 'result.json')
        top = load(directory, 'config.json')['top']
        module = load(directory, 'mapped.json')['modules'][top]
        library = load(directory, 'library_cells.json')['modules']
        parents = defaultdict(list)
        labels = defaultdict(list)
        inputs, outputs, driven = set(), set(), set()
        if not module['ports'] or not any(p['direction']=='output' for p in module['ports'].values()):
            raise ValueError('missing top output ports')
        for name, port in module['ports'].items():
            bits = port['bits']
            if not bits or any(type(b) != int and b not in ('0','1') for b in bits):
                raise ValueError('invalid or undefined port bits')
            for i, bit in enumerate(bits):
                index = port.get('offset', 0) + (len(bits)-1-i if port.get('upto') else i)
                labels[bit].append(f'{name}[{index}]')
                if port['direction'] not in ('input', 'output'):
                    raise ValueError('unsupported bidirectional port')
                (inputs if port['direction'] == 'input' else outputs).add(bit)
        aliases = defaultdict(list)
        for name, net in module.get('netnames', {}).items():
            if net.get('hide_name') or name in module['ports']:
                continue
            for i, bit in enumerate(net['bits']):
                index = net.get('offset', 0) + (len(net['bits'])-1-i if net.get('upto') else i)
                aliases[bit].append(f'{name}[{index}]')
        for name, cell in module['cells'].items():
            kind = cell['type']
            spec = library[kind]
            if int(spec['attributes'].get('abc9_box', '0'), 2) != 1:
                raise ValueError(f'unsupported non-combinational or unclassified cell {kind}')
            pins = cell['connections']
            if set(pins) != set(spec['ports']):
                raise ValueError('incomplete pin connections')
            incoming, outgoing = [], []
            for pin, bits in pins.items():
                direction = cell['port_directions'][pin]
                if direction != spec['ports'][pin]['direction'] or len(bits) != len(spec['ports'][pin]['bits']) or direction not in ('input','output'):
                    raise ValueError('invalid pin signature')
                (incoming if direction == 'input' else outgoing).extend((b, pin) for b in bits)
            for dst, op in outgoing:
                if dst in driven or dst in inputs or type(dst) != int:
                    raise ValueError('invalid or multiple driver')
                driven.add(dst)
                for src, ip in incoming:
                    parents[dst].append((src, name, kind, ip, op))
        used = outputs | {e[0] for edges in parents.values() for e in edges}
        if used - inputs - driven - {'0','1'} or any(b in ('x','z') for b in used | inputs):
            raise ValueError('undriven or undefined bit')
        graph = {b:[e[0] for e in parents[b]] for b in driven | inputs | used}
        levels, pred = {}, {}
        for bit in TopologicalSorter(graph).static_order():
            edges = parents[bit]
            levels[bit] = max((levels[e[0]]+1 for e in edges), default=0)
            pred[bit] = [e for e in edges if levels[e[0]]+1 == levels[bit]]
        depth = max(levels.values(), default=0)
        if result['status'] != 'OK' or depth != result['logic_depth']:
            raise ValueError('depth inconsistent with M1')
        found = []
        for end in sorted(levels, key=str):
            if levels[end] != depth:
                continue
            todo = [(end, [])]
            while todo and len(found)<3:
                bit, steps = todo.pop()
                if not pred[bit]:
                    found.append(dict(start=labels[bit] or [str(bit)], end=labels[end] or [str(end)], steps=steps[::-1],
                                      named_nets={str(b):aliases[b][:4] for b in [bit]+[s['dst'] for s in steps] if aliases[b]}))
                for src, cell, kind, ip, op in pred[bit]:
                    todo.append((src, steps+[dict(cell=cell,type=kind,input_pin=ip,output_pin=op,src=src,dst=bit)]))
            if len(found)==3:
                break
        return dict(status='OK',depth=depth,paths=found,rtl_source='unknown')
    except (CycleError, KeyError, OSError, ValueError, TypeError, AttributeError, IndexError) as exc:
        return dict(status='UNSUPPORTED', reason=str(exc))


def area_cells(directory):
    from .yosys_report import analyze_yosys_report
    report = analyze_yosys_report(directory)
    if report.status != 'OK':
        raise ValueError(report.message)
    library = load(directory, 'library_cells.json')['modules']
    return {k:(n, round(n*float(library[k]['attributes']['area']),6))
            for k,n in report.mapped_cell_counts.items()}


def diagnose(c, state):
    reason = c['reason']
    if reason not in ('FORMAL_FAIL','DEPTH_LIMIT','NO_AREA_IMPROVEMENT'):
        return None
    syn = c['synthesis']
    parent = state['parent']
    measured = dict(reason=reason,depth_limit=state['depth_limit'],parent_area=parent['area'],
                    parent_depth=parent['logic_depth'],area=syn.get('area'),depth=syn.get('logic_depth'))
    evidence = {}
    hypothesis = 'unknown'
    target = 'Inspect the changed output cone.'
    location = None
    try:
        if reason == 'FORMAL_FAIL':
            signals = json.loads(Path(c['equivalence']['counterexample']).read_text())['signal']
            values = {s['name']:s['data'][0] if s['wave'].startswith('=') else s['wave'][0] for s in signals}
            evidence['inputs'] = {k[3:]:v for k,v in values.items() if k.startswith('in_')}
            mismatch = {k[5:]:[v,values['gate_'+k[5:]]] for k,v in values.items() if k.startswith('gold_') and v != values['gate_'+k[5:]]}
            evidence['gold_vs_candidate'] = mismatch
            location = ','.join(sorted(mismatch)) or None
            target = 'Trace the changed logic driving the differing output for this input.'
        elif reason == 'DEPTH_LIMIT':
            cache = {}
            for role, design in [('original',state['baseline']),('parent',parent),('candidate',syn)]:
                directory = design['run_dir']
                if directory not in cache:
                    cache[directory] = paths(directory)
                report = cache[directory]
                if report['status'] != 'OK':
                    raise ValueError(report['reason'])
                path = report['paths'][0]
                evidence[role] = dict(depth=report['depth'], start=path['start'], end=path['end'],
                                      gates=[s['type'] for s in path['steps']][:32])
            location = ','.join(evidence['candidate']['end'])
            target = 'Compare the selected input/output cone against the parent. Shorten its serial dependencies.'
            hypothesis = 'This cone may admit restructuring. Gate topology alone does not identify the causal RTL expression.'
        else:
            before, after = area_cells(parent['run_dir']), area_cells(syn['run_dir'])
            rows = []
            for kind in before.keys() | after.keys():
                n,a = before.get(kind,(0,0))
                m,b = after.get(kind,(0,0))
                rows.append(dict(type=kind,parent_count=n,count=m,area=b,delta=round(b-a,6)))
            evidence['same_histogram'] = before == after
            evidence['cell_contributions'] = sorted(rows,key=lambda x:(-abs(x['delta']),-x['area'],x['type']))[:5]
            if rows:
                focus = max(rows, key=lambda row:(row['delta'],row['area'],row['type']))
                location = 'mapped family '+focus['type']
            hypothesis = ('Mapping may absorb this rewrite; equal histograms do not prove identical connectivity.'
                          if before == after else 'Added logic may offset removed cells. Inspect the positive area contributions.')
            target = 'Try a different structure affecting the listed costly cells; preserve the depth limit.'
    except (ValueError,OSError,KeyError,TypeError,IndexError) as exc:
        evidence['unavailable'] = str(exc)[:150]
    return dict(measured=measured,evidence=evidence,hypothesis=hypothesis,next_check=target,
                location=location,feasibility='combinational exploration only')
