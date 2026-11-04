`timescale 1ns/1ps


module tb_roi_fullframe;
    localparam WORDS=1203175;
    reg aclk=0;always #5 aclk=~aclk;
    reg aresetn=0;
    reg [31:0] ctrl_word=32'h180,cfg_data_word=0;
    wire [31:0] status_word,cfg_result_word;
    reg [31:0] s_axis_tdata=0;
    reg [3:0] s_axis_tkeep=0;
    reg s_axis_tlast=0,s_axis_tvalid=0;
    wire s_axis_tready;
    wire [31:0] m_axis_tdata;
    wire [3:0] m_axis_tkeep;
    wire m_axis_tlast,m_axis_tvalid;
    reg m_axis_tready=0;
    axis_roi_threshold_stats dut(.*);
    reg [31:0] inputs[0:WORDS-1],golden[0:WORDS-1];
    reg [31:0] config_words[0:69],stats[0:18];
    string data_dir,path,profile,label;
    integer f,frame_id,frame_input=0,frame_output=0,total_input=0,total_output=0;
    integer input_stalls=0,output_stalls=0,simultaneous=0,gaps=0,cycle_count=0;
    reg [31:0] rng=32'h234abcde;
    bit frame_active=0,input_stalled=0,output_stalled=0;
    reg [36:0] held_output,held_input;
    integer i;

    always @(negedge aclk) begin
        rng={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};
        m_axis_tready=aresetn && (!frame_active || rng[0] || rng[3]);
        if(!aresetn || !frame_active)s_axis_tvalid=0;
        else if(!input_stalled)begin
            if(frame_input<WORDS && (rng[4] || rng[8] || rng[15]))begin
                s_axis_tvalid=1;s_axis_tdata=inputs[frame_input];
                s_axis_tkeep=15;s_axis_tlast=frame_input==WORDS-1;
            end else begin s_axis_tvalid=0;gaps=gaps+1;end
        end
    end
    always @(posedge aclk)begin
        cycle_count=cycle_count+1;
        if(cycle_count>20000000)$fatal(1,"Fullframe timeout");
        if(aresetn)begin
            if(input_stalled && {s_axis_tvalid,s_axis_tdata,s_axis_tkeep,s_axis_tlast}!=={1'b1,held_input})$fatal(1,"Source changed under stall");
            if(output_stalled && {m_axis_tvalid,m_axis_tdata,m_axis_tkeep,m_axis_tlast}!=={1'b1,held_output})$fatal(1,"DUT changed under stall");
            if(s_axis_tvalid && s_axis_tready)begin
                if(!frame_active||frame_input>=WORDS)$fatal(1,"Unexpected input");
                if({s_axis_tdata,s_axis_tkeep,s_axis_tlast}!=={inputs[frame_input],4'hf,(frame_input==WORDS-1)})$fatal(1,"Stimulus mismatch");
                frame_input=frame_input+1;total_input=total_input+1;
            end
            if(m_axis_tvalid && m_axis_tready)begin
                if(!frame_active||frame_output>=WORDS||frame_output>=frame_input)$fatal(1,"Unexpected output");
                if({m_axis_tdata,m_axis_tkeep,m_axis_tlast}!=={golden[frame_output],4'hf,(frame_output==WORDS-1)})
                    $fatal(1,"File golden mismatch frame=%0d beat=%0d got=%08h wanted=%08h",frame_id,frame_output,m_axis_tdata,golden[frame_output]);
                frame_output=frame_output+1;total_output=total_output+1;
            end
            if(s_axis_tvalid && !s_axis_tready)input_stalls=input_stalls+1;
            if(m_axis_tvalid && !m_axis_tready)output_stalls=output_stalls+1;
            if(s_axis_tvalid && s_axis_tready && m_axis_tvalid && m_axis_tready)simultaneous=simultaneous+1;
            input_stalled=s_axis_tvalid && !s_axis_tready;
            output_stalled=m_axis_tvalid && !m_axis_tready;
            held_input={s_axis_tdata,s_axis_tkeep,s_axis_tlast};
            held_output={m_axis_tdata,m_axis_tkeep,m_axis_tlast};
        end else begin input_stalled=0;output_stalled=0;end
    end
    task load_config(input string name);
        begin
            path=$sformatf("%s/%s_config.mem",data_dir,name);
            f=$fopen(path,"r");if(!f)$fatal(1,"Missing %s",path);$fclose(f);
            $readmemh(path,config_words);
            cfg_write(1,0);cfg_write(0,config_words[0]);
            for(integer k=0;k<68;k=k+1)cfg_write(8'h10+k,config_words[2+k]);
            cfg_write(1,config_words[1]);
        end
    endtask
    task cfg_write(input [7:0] address,input [31:0] value);
        integer timeout;reg token;
        begin
            @(negedge aclk);cfg_data_word=value;
            @(negedge aclk);ctrl_word[23:16]=address;token=!ctrl_word[12];ctrl_word[12]=token;
            timeout=0;
            while(status_word[2]!==token || status_word[0])begin
                @(posedge aclk);#2;timeout=timeout+1;if(timeout>50)$fatal(1,"cfg timeout");
            end
            if(status_word[3])$fatal(1,"cfg rejected");
            @(negedge aclk);if(cfg_result_word!==value)$fatal(1,"cfg readback mismatch");
        end
    endtask
    initial begin
        if(!$value$plusargs("DATA_DIR=%s",data_dir))$fatal(1,"Need +DATA_DIR");
        path={data_dir,"/input_gray.mem"};
        f=$fopen(path,"r");if(!f)$fatal(1,"Missing input");$fclose(f);$readmemh(path,inputs);
        repeat(4)@(negedge aclk);aresetn=1;
        for(frame_id=1;frame_id<=3;frame_id=frame_id+1)begin
            if(frame_id==1)begin profile="latest17";label="full";end
            else if(frame_id==2)begin profile="latest17";label="latest17";end
            else begin profile="frozen16";label="frozen16";end
            if(frame_id!=2)load_config(profile);
            path=$sformatf("%s/%s_mask.mem",data_dir,label);$readmemh(path,golden);
            path=$sformatf("%s/%s_stats.mem",data_dir,label);$readmemh(path,stats);
            @(negedge aclk);ctrl_word[7:0]=120;ctrl_word[8]=0;ctrl_word[10]=1;ctrl_word[11]=frame_id!=1;
            ctrl_word[23:16]=2;frame_input=0;frame_output=0;frame_active=1;
            $display("FRAME_START id=%0d profile=%s roi_mode=%b",frame_id,profile,ctrl_word[11]);
            while(frame_output<WORDS)@(negedge aclk);
            @(negedge aclk);frame_active=0;
            if(status_word[4:3] || status_word[1:0])$fatal(1,"Final status invalid %h",status_word);
            if(status_word[31:16]!==frame_id)$fatal(1,"Completion mismatch");
            if(cfg_result_word!==stats[0])$fatal(1,"White count mismatch %0d %0d",cfg_result_word,stats[0]);
            @(negedge aclk);ctrl_word[23:16]=3;#1;
            if(cfg_result_word!==stats[1])$fatal(1,"Pixel count mismatch");
            for(i=0;i<17;i=i+1)begin
                @(negedge aclk);ctrl_word[23:16]=8'h80+i;#1;
                if(cfg_result_word!==stats[2+i])$fatal(1,"ROI count mismatch frame=%0d index=%0d got=%0d expected=%0d",frame_id,i,cfg_result_word,stats[2+i]);
            end
            $display("FRAME_PASS id=%0d input=%0d output=%0d bytes=4812700 white=%0d pixels=%0d roi_counts=17",frame_id,frame_input,frame_output,stats[0],stats[1]);
        end
        $display("PASS: ROI_STATS_FULLFRAME frames=3 input_beats=%0d output_beats=%0d bytes=14438100 input_stalls=%0d output_stalls=%0d simultaneous=%0d gaps=%0d",total_input,total_output,input_stalls,output_stalls,simultaneous,gaps);
        $finish;
    end
endmodule
