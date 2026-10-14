`timescale 1ns/10ps
// 파이프 기반 lockstep 테스트벤치: 한 줄 읽기 = i_clk 1사이클
module tb_pipe;
    parameter PERIOD = 100;
    reg  i_clk = 0, i_rstn = 0, i_ssn = 1, i_sclk = 0, i_mosi = 0;
    wire o_miso, o_cmd_done;
`ifdef RTL
    TOP dut (.i_clk(i_clk), .i_rstn(i_rstn), .i_ssn(i_ssn), .i_mosi(i_mosi),
             .i_sclk(i_sclk), .o_miso(o_miso), .o_cmd_done(o_cmd_done));
`else
    supply1 VDD; supply0 VSS;
    TOP dut (.i_clk(i_clk), .i_rstn(i_rstn), .i_ssn(i_ssn), .i_mosi(i_mosi),
             .i_sclk(i_sclk), .o_miso(o_miso), .o_cmd_done(o_cmd_done), .VDD(VDD), .VSS(VSS));
`endif
    integer fin, fout, r; reg [3:0] v;
    function [7:0] bc(input b); bc = (b===1'b1)?"1":(b===1'b0)?"0":"x"; endfunction
    always #(PERIOD/2) i_clk = ~i_clk;
    initial begin
        fin = $fopen("to_sim", "r"); fout = $fopen("from_sim", "w");
        @(negedge i_clk);
        forever begin
            r = $fscanf(fin, "%b", v);
            if (r != 1) begin $fclose(fout); $finish; end
            #1; {i_rstn, i_ssn, i_sclk, i_mosi} = v;
            @(posedge i_clk); #(PERIOD/2 - 2);
            $fwrite(fout, "%s%s\n", bc(o_miso), bc(o_cmd_done)); $fflush(fout);
            @(negedge i_clk);
        end
    end
endmodule
