"""Compiled serial LangGraph workflow, using M1/M2 as authoritative tools."""
from langgraph.graph import StateGraph, START, END

from rtl_agent.eda import EDAConfig
from rtl_agent.report.report_generator import final_report
from .state import AgentState, SearchBudget
from . import nodes


def build_graph(model, *, original_rtl, top, config=None, budget=None, outdir=None,
                depth_limit=None, extra_sources=(), diagnostics=False):
    config = config or EDAConfig.from_tools_env()
    budget = budget or SearchBudget()
    graph = StateGraph(AgentState)
    graph.add_node('initialize', lambda s: nodes.initialize(original_rtl, top, config, budget, outdir, depth_limit, extra_sources,
                   getattr(model, 'metadata', {'mode': 'mock'}), diagnostics))
    graph.add_node('baseline', nodes.baseline_node)
    graph.add_node('controller', nodes.control_node)
    graph.add_node('select_strategy', nodes.select_node)
    graph.add_node('generate', lambda s: nodes.generate_node(s, model))
    graph.add_node('precheck', nodes.precheck_node)
    graph.add_node('verify', nodes.verify_node)
    graph.add_node('synthesize_evaluate', nodes.synthesize_node)
    graph.add_node('record', nodes.record_node)
    graph.add_node('final_report', final_report)
    graph.add_edge(START, 'initialize')
    graph.add_edge('initialize', 'baseline')
    graph.add_edge('baseline', 'controller')
    graph.add_conditional_edges('controller', lambda s: 'final_report' if s['stop_reason'] else 'select_strategy', ['final_report', 'select_strategy'])
    graph.add_conditional_edges('select_strategy', lambda s: 'final_report' if s['stop_reason'] else 'generate', ['final_report', 'generate'])
    graph.add_conditional_edges('generate', lambda s: 'precheck' if s['current']['reason'] == 'GENERATED' else 'record', ['precheck', 'record'])
    graph.add_conditional_edges('precheck', lambda s: 'verify' if s['current']['reason'] == 'PRECHECK_OK' else 'record', ['verify', 'record'])
    graph.add_conditional_edges('verify', lambda s: 'synthesize_evaluate' if s['current']['reason'] == 'FORMAL_PASS' else 'record', ['synthesize_evaluate', 'record'])
    graph.add_edge('synthesize_evaluate', 'record')
    graph.add_edge('record', 'controller')
    graph.add_edge('final_report', END)
    return graph.compile()


def run_search(model, **kwargs):
    budget = kwargs.get('budget') or SearchBudget()
    graph = build_graph(model, **kwargs)
    return graph.invoke({}, {'recursion_limit': 16 * budget.max_rounds + 20})
