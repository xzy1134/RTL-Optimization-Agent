from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from rtl_agent.eda import EDAConfig, synthesize_rtl, verify_rtl
from rtl_agent.eda.common import ROOT, digest


class EDATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep logs under ignored work/ for review after the test process exits.
        root = ROOT / "work" / "rtl_agent"
        root.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix="m1-tests-", dir=root))
        cls.config = EDAConfig.from_tools_env(timeout_seconds=20)
        print(f"\nM1 artifacts: {cls.root}", flush=True)

    def source(self, name, text):
        path = self.root / (self._testMethodName + "-" + name + ".v")
        path.write_text(text)
        return path

    def verify(self, gold, gate, top="t", **kwargs):
        return verify_rtl(gold, gate, top, config=kwargs.pop("config", self.config), outdir=self.root, **kwargs)

    def test_baseline_and_comparable_repeat(self):
        source = ROOT / "case/rtl/opt_top.v"
        original_hash = digest(source)
        a = synthesize_rtl(source, "opt_top", config=self.config, outdir=self.root)
        b = synthesize_rtl(source, "opt_top", config=self.config, outdir=self.root)
        self.assertEqual(a.status, "OK", a)
        self.assertAlmostEqual(a.area, 764.218, places=3)
        self.assertEqual(a.logic_depth, 19)
        self.assertEqual((a.area, a.logic_depth, a.config_fingerprint), (b.area, b.logic_depth, b.config_fingerprint))
        self.assertNotEqual(a.run_dir, b.run_dir)
        self.assertEqual(digest(source), original_hash)
        self.assertTrue((Path(a.run_dir) / "config.json").exists())

    def test_self_equivalence_opt_top(self):
        p = ROOT / "case/rtl/opt_top.v"
        self.assertEqual(self.verify(p, p, "opt_top").status, "PASS")

    def test_structural_rewrite_and_duplicate_helper_names(self):
        a = self.source("gold", """
module helper(input [3:0] a,b,c,input s,output [3:0] y);
assign y=s ? (a+b):(a+c); endmodule
module t(input [3:0] a,b,c,input s,output [3:0] y);
helper u(a,b,c,s,y); endmodule
""")
        b = self.source("candidate", """
module helper(input [3:0] a,b,c,input s,output [3:0] y);
assign y=a+(s?b:c); endmodule
module t(input [3:0] a,b,c,input s,output [3:0] y);
helper u(a,b,c,s,y); endmodule
""")
        self.assertEqual(self.verify(a, b).status, "PASS")

    def test_counterexample(self):
        a = self.source("gold", "module t(input [3:0] a,b,output [3:0] y); assign y=a+b; endmodule")
        b = self.source("bad", "module t(input [3:0] a,b,output [3:0] y); assign y=a-b; endmodule")
        r = self.verify(a, b)
        self.assertEqual(r.status, "FAIL", r)
        self.assertTrue(Path(r.counterexample).is_file())
        self.assertTrue(json.loads(Path(r.counterexample).read_text())["signal"])

    def test_syntax_missing_file_and_wrong_top(self):
        a = self.source("valid", "module t(input a,output y); assign y=a; endmodule")
        b = self.source("invalid", "module t(input a,output y); assign y=; endmodule")
        self.assertEqual(self.verify(a, b).status, "ERROR")
        self.assertEqual(self.verify(a, self.root / "missing.v").status, "ERROR")
        self.assertEqual(self.verify(a, a, "wrong_top").status, "ERROR")
        self.assertEqual(synthesize_rtl(b, "t", config=self.config, outdir=self.root).status, "ERROR")

    def test_interface_mismatch(self):
        a = self.source("a", "module t(input a,output y); assign y=a; endmodule")
        b = self.source("b", "module t(input [1:0] a,output y); assign y=^a; endmodule")
        self.assertEqual(self.verify(a, b).status, "ERROR")

    def test_sequential_is_unknown(self):
        a = self.source("seq", "module t(input clk,a,output reg y); always @(posedge clk) y<=a; endmodule")
        r = self.verify(a, a)
        self.assertEqual(r.status, "UNKNOWN", r)
        self.assertIn("$dff", r.message)

    def test_undefined_is_unknown(self):
        a = self.source("undef", "module t(input a,output y); assign y=a?1'bx:1'b0; endmodule")
        self.assertEqual(self.verify(a, a).status, "UNKNOWN")

    def test_missing_tool_and_library(self):
        a = self.source("a", "module t(input a,output y); assign y=~a; endmodule")
        self.assertEqual(self.verify(a, a, config=replace(self.config, yosys_bin="/no/such/yosys")).status, "ERROR")
        r = synthesize_rtl(a, "t", config=replace(self.config, liberty_file="/no/such/lib"), outdir=self.root)
        self.assertEqual(r.status, "ERROR")

    def test_timeout_statuses(self):
        # Exercise a real process timeout without relying on difficult SAT instances.
        tool = self.root / "slow-yosys"
        tool.write_text("#!/usr/bin/python3\nimport sys,time\nif '-V' in sys.argv: print('fake timeout test tool')\nelse: time.sleep(10)\n")
        tool.chmod(0o755)
        config = replace(self.config, yosys_bin=str(tool), timeout_seconds=0.2)
        a = self.source("a", "module t(input a,output y); assign y=~a; endmodule")
        self.assertEqual(self.verify(a, a, config=config).status, "UNKNOWN")
        self.assertEqual(synthesize_rtl(a, "t", config=config, outdir=self.root).status, "TIMEOUT")

    def test_wiring_zero_depth(self):
        a = self.source("a", "module t(input a,output y); assign y=a; endmodule")
        r = synthesize_rtl(a, "t", config=self.config, outdir=self.root)
        self.assertEqual(r.status, "OK", r)
        self.assertEqual((r.area, r.logic_depth), (0.0, 0))
        self.assertEqual(self.verify(a, a).status, "PASS")


    def test_external_blackbox_area_is_rejected(self):
        a = self.source("a", """
(* blackbox *) module external(input a, output y); endmodule
module t(input a, output y); external u(a,y); endmodule
""")
        r = synthesize_rtl(a, "t", config=self.config, outdir=self.root)
        self.assertEqual(r.status, "ERROR", r)
        self.assertIn("Incomplete Liberty mapping", r.message)
        self.assertEqual(self.verify(a, a).status, "UNKNOWN")

    def test_multiple_sources_and_space_paths(self):
        d = self.root / "sources with spaces"
        d.mkdir(exist_ok=True)
        a = d / "top.v"
        b = d / "helper.v"
        a.write_text("module t(input a, output y); helper u(a,y); endmodule")
        b.write_text("module helper(input a, output y); assign y=~a; endmodule")
        self.assertEqual(self.verify(a, a, original_sources=[b], candidate_sources=[b]).status, "PASS")
        r = synthesize_rtl(a, "t", config=self.config, outdir=self.root, extra_sources=[b])
        self.assertEqual(r.status, "OK", r)

    def test_signedness_parameter_and_loop(self):
        a = self.source("a", "module t #(parameter W=1)(input a,output y); assign y=a; endmodule")
        b = self.source("b", "module t #(parameter W=2)(input a,output y); assign y=a; endmodule")
        self.assertEqual(self.verify(a, b).status, "ERROR")
        a = self.source("signed", "module t(input signed [3:0] a,output [3:0] y); assign y=a; endmodule")
        b = self.source("unsigned", "module t(input [3:0] a,output [3:0] y); assign y=a; endmodule")
        self.assertEqual(self.verify(a, b).status, "ERROR")
        a = self.source("loop", "module t(input a, output y); assign y=~y; endmodule")
        self.assertEqual(self.verify(a, a).status, "ERROR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
