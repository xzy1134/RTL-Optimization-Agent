module t #(parameter W=3)(
    input [W-1:0] a,b,c,d,
    input s,
    output [2*W-1:0] y
);
    wire [W-1:0] x=s?c:a, z=s?d:b;
    assign y=x*z;
endmodule
