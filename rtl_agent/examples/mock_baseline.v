// Small combinational fixture for real EDA integration tests, not a benchmark replacement.
module t #(parameter W=3)(
    input [W-1:0] a,b,c,d,
    input s,
    output [2*W-1:0] y
);
    wire [2*W-1:0] p=a*b, q=c*d;
    assign y=s?q:p;
endmodule
