from .rtl_features import Evidence, RTLFeatures, analyze_netlist
from .yosys_report import YosysReport, analyze_yosys_report


def analyze_rtl(*args, **kwargs):
    from rtl_agent.eda.analysis import analyze_rtl as implementation
    return implementation(*args, **kwargs)


__all__ = ['Evidence', 'RTLFeatures', 'YosysReport', 'analyze_rtl', 'analyze_netlist', 'analyze_yosys_report']
