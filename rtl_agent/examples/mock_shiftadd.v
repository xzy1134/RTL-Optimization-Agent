module t #(parameter W=3)(
    input [W-1:0] a,b,c,d,
    input s,
    output [2*W-1:0] y
);
    wire [W-1:0] x=s?c:a, z=s?d:b;
    wire [2*W-1:0] partial [0:W-1];
    wire [2*W-1:0] sum [0:W];
    assign sum[0]=0;
    genvar i;
    generate for (i=0;i<W;i=i+1) begin: products
        assign partial[i]=({{W{1'b0}}, (x & {W{z[i]}})} << i);
        assign sum[i+1]=sum[i]+partial[i];
    end endgenerate
    assign y=sum[W];
endmodule
