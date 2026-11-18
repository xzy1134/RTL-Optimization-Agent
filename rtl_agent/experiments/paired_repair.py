"""Five independent pairs from a frozen addsubz failure. Pass --live to spend quota."""
import argparse
import difflib
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import time

from rtl_agent.agent.evaluator import assess
from rtl_agent.analysis.diagnostics import load, paths
from rtl_agent.eda import EDAConfig, synthesize_rtl, verify_rtl
from rtl_agent.eda.common import ROOT, digest, new_run, save_json
from rtl_agent.llm.client import ModelError, ModelRequest
from rtl_agent.llm.deepseek_client import DeepSeekClient
from rtl_agent.llm.prompt_builder import build_messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--ablation', action='store_true', help='Three arms, three balanced blocks')
    args = parser.parse_args()
    source = ROOT/'work/rtl_agent/diagnostic-ab-taabqux3/deepseek-search-t2beoxec'
    record = load(source/'round-0001','record.json')
    baseline = load(source,'report.json')['baseline']
    experiment = load(source,'experiment.json')
    config = EDAConfig(**experiment['eda_config'])
    original = Path(baseline['rtl_path'])
    failed = source/'round-0001/candidate.v'
    syn = record['synthesis']
    assert digest(failed) == record['source_hash'] and digest(config.liberty_file)==experiment['library_sha256']
    assert record['equivalence']['status']=='PASS' and (syn['area'],syn['logic_depth'])==(73.682,11)
    assert syn['config_fingerprint']==baseline['config_fingerprint']
    route = paths(syn['run_dir'])
    assert route['status']=='OK' and route['depth']==11
    path = route['paths'][0]
    data = load(source/'round-0002','request.json')
    assert data['original_rtl']==original.read_text() and len(data['feedback'])==1
    data['parent_rtl']=failed.read_text()
    data['metrics'].update(parent_area=syn['area'],parent_depth=11)
    data['feedback'][0].pop('diagnosis')
    data['memory']['objective']='Repair parent_rtl. Keep area below 75.544 and depth at most 10; if possible retain area <=73.682.'
    data['constraints']['api_budget']=dict(remaining_requests=1,remaining_tokens=25000)
    a = ModelRequest(**data, feedback_mode='diagnostic')
    b = deepcopy(a)
    b.feedback[0]['path_diagnosis']=dict(start=path['start'],end=path['end'],gates=[s['type'] for s in path['steps']],
        inspect='Inspect the dependencies along this input-to-output cone for possible same-cycle restructuring.')
    requests=dict(A=a,B=b)
    orders = [('A','B') if i%2==0 else ('B','A') for i in range(5)]
    if args.ablation:
        # All arms get the same minimal task. The failed source itself supplies the edit history.
        data.update(strategies=[], primary_strategy='open_exploration',
                    feedback=[dict(equivalence='PASS',reason='DEPTH_LIMIT')],
                    memory={'objective':data['memory']['objective']})
        data['constraints']['api_budget']['remaining_tokens']=12000
        a=ModelRequest(**data, feedback_mode='diagnostic')
        facts=deepcopy(a)
        facts.feedback[0]['path']={'measured_depth':route['depth'],'evidence':{
            'start':path['start'],'end':path['end'],'connections':path['steps']}}
        advice=deepcopy(facts)
        diagnostic=load(source/'round-0001','diagnosis.json')
        advice.feedback[0]['next_check']=diagnostic['next_check']
        requests={'A':a,'B1':facts,'B2':advice}
        orders=[('A','B1','B2'),('B1','B2','A'),('B2','A','B1')]
    messages={k:build_messages(v) for k,v in requests.items()}
    def context(k):
        return json.loads(messages[k][1]['content'].split('\n',1)[1])
    if args.ablation:
        check=context('B2')
        check['feedback'][0].pop('next_check')
        assert check==context('B1')
        check['feedback'][0].pop('path')
        assert check==context('A')
        assert len({v[0]['content'] for v in messages.values()})==1
    else:
        check=context('B')
        check['feedback'][0].pop('path_diagnosis')
        assert check==context('A') and messages['A'][0]==messages['B'][0]
    root=new_run(None,'addsubz-ablation' if args.ablation else 'addsubz-paired')
    print(root,flush=True)
    for k,m in messages.items():
        save_json(root/(k+'-messages.json'),m)
    save_json(root/'design.json',dict(seed=str(failed),hash=digest(failed),path=route,config=config.__dict__,fingerprint=baseline['config_fingerprint']))
    if not args.live:
        return
    client=DeepSeekClient(model='deepseek-flash',api_url='https://api.deepseek.com/chat/completions',max_retries=0)
    rows=[]
    for pair,order in enumerate(orders,1):
        for arm in order:
            start=time.perf_counter()
            run=root/f'{pair}-{arm}'
            run.mkdir()
            row=dict(pair=pair,arm=arm,area=None,depth=None,proof='NOT_RUN',success=False,strict=False)
            try:
                reply=client.generate(requests[arm],timeout=60)
                usage=reply.usage
                save_json(run/'response.json',asdict(reply))
                row['reason']=reply.action
                if reply.action=='CANDIDATE':
                    rtl=run/'candidate.v'
                    rtl.write_text(reply.rtl)
                    (run/'change.diff').write_text(''.join(difflib.unified_diff(failed.read_text().splitlines(True),reply.rtl.splitlines(True),fromfile=failed.name,tofile='candidate.v')))
                    proof=verify_rtl(original,rtl,'TopModule',config=config,outdir=run)
                    row.update(proof=proof.status,reason='FORMAL_'+proof.status)
                    if proof.status=='PASS':
                        result=synthesize_rtl(rtl,'TopModule',config=config,outdir=run)
                        gate=assess(asdict(proof),asdict(result),baseline['config_fingerprint'],10,baseline)
                        row.update(area=result.area,depth=result.logic_depth,reason=gate['reason'],
                                   success=gate['accepted'],strict=gate['accepted'] and result.area<=73.682)
            except ModelError as error:
                usage=error.usage
                row['reason']=error.code
                save_json(run/'model_error.json',error.artifacts)
            row.update(usage=usage,seconds=time.perf_counter()-start)
            rows.append(row)
            save_json(root/'results.json',rows)
            print(pair,arm,row['reason'],row['area'],row['depth'],flush=True)
            if usage['unknown_usage_calls'] or row['reason'].startswith('HTTP_'):
                return


if __name__=='__main__':
    main()
