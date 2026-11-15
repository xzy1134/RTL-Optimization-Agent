// Small alternate multiplication implementation for real depth-gate testing.
module t #(parameter W=3)(input [W-1:0] a,b,c,d,input s,output reg [2*W-1:0] y);
    wire [W-1:0] x=s?c:a, z=s?d:b;
    integer k;
    always @* begin
        y=0;
        for (k=0; k<(2**W); k=k+1)
            if (x==k) y=z*k;
    end
endmodule
