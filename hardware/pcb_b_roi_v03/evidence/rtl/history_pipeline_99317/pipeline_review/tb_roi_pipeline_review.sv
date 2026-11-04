`timescale 1ns/1ps

module tb_roi_pipeline_review;
    localparam N=17, Q=32768, P=256;
    reg aclk=0; always #5 aclk=~aclk;
    reg aresetn=0;
    reg [31:0] ctrl_word=32'h180, cfg_data_word=0;
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

    integer rw=0,rh=0;
    integer x0[0:N-1],y0[0:N-1],x1[0:N-1],y1[0:N-1];
    reg [N-1:0] enabled=0;
    reg [31:0] qdata[0:Q-1];
    reg [3:0] qkeep[0:Q-1];
    reg qlast[0:Q-1];
    integer pa[0:P-1][0:N-1],pw[0:P-1],pp[0:P-1];
    bit pe[0:P-1];
    integer wa[0:N-1],lw_area[0:N-1];
    integer wr=0,rd=0,ipackets=0,opackets=0,expected_completed=0;
    integer pixel_index=0,white_work=0;
    integer last_white=0,last_pixels=0;
    bit ref_inpacket=0,work_bad=0,last_bad=0;
    reg [11:0] locked_mode;
    integer total_input=0,total_output=0,total_packets=0;
    integer input_stalls=0,output_stalls=0,back_to_back=0,zero_keep=0;
    reg [36:0] held_output;
    bit was_stalled=0;
    integer ready_mode=2;
    reg [31:0] rng=32'h35a8bc91;
    integer i,j,lane,xx,yy,pixel,transformed,foreground,any_hit,hit;
    integer completed_now;
    reg [31:0] expected_word;
    bit reset_now,output_last_now;

    always @(negedge aclk) begin
        rng={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};
        m_axis_tready = aresetn && (ready_mode==1 || (ready_mode==2 && (rng[0] || rng[3])));
    end

    always @(posedge aclk) begin
        reset_now = !aresetn || ctrl_word[9];
        output_last_now=0;
        if (reset_now) begin
            wr=0;rd=0;ipackets=0;opackets=0;expected_completed=0;
            pixel_index=0;white_work=0;last_white=0;last_pixels=0;
            ref_inpacket=0;work_bad=0;last_bad=0;was_stalled=0;
            for (i=0;i<N;i=i+1) begin wa[i]=0;lw_area[i]=0; end
            if (!aresetn) begin
                rw=0;rh=0;enabled=0;
                for(i=0;i<N;i=i+1) begin x0[i]=0;y0[i]=0;x1[i]=0;y1[i]=0;end
            end
        end else begin
            if (was_stalled && (m_axis_tvalid!==1'b1 ||
                {m_axis_tdata,m_axis_tkeep,m_axis_tlast}!==held_output))
                $fatal(1,"Output changed under backpressure");
            if (s_axis_tvalid && !s_axis_tready) input_stalls=input_stalls+1;
            if (m_axis_tvalid && !m_axis_tready) output_stalls=output_stalls+1;
            if (s_axis_tvalid && s_axis_tready) begin
                if(wr>=Q)$fatal(1,"TB queue exhausted");
                if (!ref_inpacket) begin
                    pixel_index=0;white_work=0;work_bad=0;locked_mode=ctrl_word[11:0];
                    for(i=0;i<N;i=i+1)wa[i]=0;
                end
                if(rw==0 || rh==0)work_bad=1;
                expected_word=0;
                for(lane=0;lane<4;lane=lane+1) begin
                    if(s_axis_tkeep[lane])begin
                        pixel=(s_axis_tdata>>(lane*8))&255;
                        transformed=locked_mode[10] ? 255-pixel : pixel;
                        foreground=transformed>locked_mode[7:0];
                        if(rw>0)begin xx=pixel_index%rw;yy=pixel_index/rw;end
                        else begin xx=0;yy=0;end
                        if(rw==0||rh==0||pixel_index>=rw*rh)work_bad=1;
                        any_hit=0;
                        for(i=0;i<N;i=i+1)begin
                            hit=enabled[i] && rw>0 && rh>0 && pixel_index<rw*rh &&
                                xx>=x0[i] && xx<x1[i] && yy>=y0[i] && yy<y1[i];
                            any_hit=any_hit||hit;
                            if(!locked_mode[8] && hit && foreground)wa[i]=wa[i]+1;
                        end
                        if(locked_mode[8])expected_word[lane*8+:8]=pixel;
                        else if(foreground && (!locked_mode[11]||any_hit))begin
                            expected_word[lane*8+:8]=255;white_work=white_work+1;
                        end
                        pixel_index=pixel_index+1;
                    end
                end
                qdata[wr]=expected_word;qkeep[wr]=s_axis_tkeep;qlast[wr]=s_axis_tlast;wr=wr+1;
                total_input=total_input+1;
                if(s_axis_tkeep==0)zero_keep=zero_keep+1;
                ref_inpacket=!s_axis_tlast;
                if(s_axis_tlast)begin
                    if(ipackets>=P)$fatal(1,"TB packet queue exhausted");
                    for(i=0;i<N;i=i+1)pa[ipackets][i]=wa[i];
                    pw[ipackets]=white_work;pp[ipackets]=pixel_index;
                    pe[ipackets]=work_bad || pixel_index!=rw*rh || rw==0 || rh==0;
                    ipackets=ipackets+1;
                end
            end
            if(m_axis_tvalid && m_axis_tready)begin
                if(rd>=wr)$fatal(1,"Unexpected output rd=%0d wr=%0d packets=%0d cfg=%h",rd,wr,total_packets,ctrl_word);
                if({m_axis_tdata,m_axis_tkeep,m_axis_tlast}!=={qdata[rd],qkeep[rd],qlast[rd]})
                    $fatal(1,"Data mismatch beat=%0d got=%08h/%h/%b want=%08h/%h/%b",rd,m_axis_tdata,m_axis_tkeep,m_axis_tlast,qdata[rd],qkeep[rd],qlast[rd]);
                rd=rd+1;total_output=total_output+1;
                if(m_axis_tlast)begin
                    output_last_now=1;
                    last_white=pw[opackets];last_pixels=pp[opackets];last_bad=pe[opackets];
                    for(i=0;i<N;i=i+1)lw_area[i]=pa[opackets][i];
                    opackets=opackets+1;total_packets=total_packets+1;
                    expected_completed=(expected_completed+1)&65535;
                    if(s_axis_tvalid && s_axis_tready)back_to_back=back_to_back+1;
                end
            end
            was_stalled=m_axis_tvalid && !m_axis_tready;
            held_output={m_axis_tdata,m_axis_tkeep,m_axis_tlast};
        end
        #1;
        if(status_word[15:8]!==8'h03)$fatal(1,"ABI mismatch");
        if(status_word[31:16]!==expected_completed[15:0])$fatal(1,"Completion count mismatch");
        if(status_word[4]!==last_bad)$fatal(1,"Frame error commit mismatch expected=%b actual=%b",last_bad,status_word[4]);
        
        
        if(ctrl_word[23:16]==2 && cfg_result_word!==last_white)$fatal(1,"White snapshot committed early/incorrectly");
        if(ctrl_word[23:16]==3 && cfg_result_word!==last_pixels)$fatal(1,"Pixel snapshot incorrect");
        if(ctrl_word[23:16]>=8'h80 && ctrl_word[23:16]<8'h80+N)
            if(cfg_result_word!==lw_area[ctrl_word[23:16]-8'h80])$fatal(1,"ROI snapshot incorrect");
    end

    task cfg_write(input [7:0] address,input [31:0] value);
        integer timeout;
        reg token;
        begin
            while(status_word[0])@(negedge aclk);
            @(negedge aclk);cfg_data_word=value;
            @(negedge aclk);token=!ctrl_word[12];ctrl_word[23:16]=address;ctrl_word[12]=token;
            timeout=0;
            while(status_word[2]!==token || status_word[0])begin
                @(posedge aclk);#2;timeout=timeout+1;
                if(timeout>50)$fatal(1,"Configuration ack timeout addr=%h",address);
            end
            if(status_word[3])$fatal(1,"Unexpected cfg_error addr=%h",address);
            @(negedge aclk);
            if(address==0)begin rw=value[15:0];rh=value[31:16];end
            else if(address==1)enabled=value[N-1:0];
            else if(address>=8'h10 && address<8'h10+4*N)begin
                case(address[1:0])
                    0:x0[(address-8'h10)>>2]=value;
                    1:y0[(address-8'h10)>>2]=value;
                    2:x1[(address-8'h10)>>2]=value;
                    3:y1[(address-8'h10)>>2]=value;
                endcase
            end
            if(cfg_result_word!==value)$fatal(1,"Configuration readback mismatch addr=%h got=%h expected=%h",address,cfg_result_word,value);
        end
    endtask

    task set_mode(input [7:0] threshold,input bit bypass,input bit invert,input bit roi_mode);
        begin
            @(negedge aclk);ctrl_word[7:0]=threshold;ctrl_word[8]=bypass;
            ctrl_word[10]=invert;ctrl_word[11]=roi_mode;ctrl_word[23:16]=2;
        end
    endtask
    task set_roi(input integer index,input integer ax,input integer ay,input integer bx,input integer by);
        begin
            cfg_write(8'h10+4*index,ax);cfg_write(8'h11+4*index,ay);
            cfg_write(8'h12+4*index,bx);cfg_write(8'h13+4*index,by);
        end
    endtask
    task drain;
        integer timeout;
        begin
            timeout=0;
            while(rd!=wr || status_word[0])begin
                @(negedge aclk);timeout=timeout+1;
                if(timeout>200)$fatal(1,"Drain timeout");
            end
        end
    endtask
    task check_stats;
        integer k;
        begin
            drain();
            for(k=0;k<N;k=k+1)begin
                @(negedge aclk);ctrl_word[23:16]=8'h80+k;
                @(posedge aclk);#2;
                if(cfg_result_word!==lw_area[k])$fatal(1,"Area mismatch ROI %0d",k);
            end
            @(negedge aclk);ctrl_word[23:16]=2;
        end
    endtask
    function [7:0] pattern(input integer index);
        case(index%5)0:pattern=0;1:pattern=134;2:pattern=135;3:pattern=255;default:pattern=120;endcase
    endfunction
    task beat(input [31:0] data,input [3:0] keep,input bit last);
        integer timeout;
        begin
            @(negedge aclk);s_axis_tdata=data;s_axis_tkeep=keep;s_axis_tlast=last;s_axis_tvalid=1;
            timeout=0;
            @(posedge aclk);
            while(!s_axis_tready)begin @(posedge aclk);timeout=timeout+1;if(timeout>200)$fatal(1,"Input timeout");end
            @(negedge aclk);s_axis_tvalid=0;
        end
    endtask
    task frame(input integer bytes,input bit sparse,input bit mutate);
        integer sent,wordno,k,available;
        reg [3:0] keep;
        reg [31:0] data;
        reg [11:0] saved_mode;
        begin
            sent=0;wordno=0;saved_mode=ctrl_word[11:0];
            while(sent<bytes)begin
                keep=sparse ? (wordno%16) : 15;data=0;
                for(k=0;k<4;k=k+1)begin
                    if(keep[k])begin
                        if(sent<bytes)begin data[k*8+:8]=pattern(sent);sent=sent+1;end
                        else keep[k]=0;
                    end
                end
                beat(data,keep,sent==bytes);
                if(mutate && wordno==0)begin ctrl_word[7:0]=0;ctrl_word[8]=!saved_mode[8];ctrl_word[10]=!saved_mode[10];ctrl_word[11]=!saved_mode[11];end
                wordno=wordno+1;
            end
            ctrl_word[11:0]=saved_mode;
            drain();check_stats();
        end
    endtask
    task soft_reset;
        begin
            @(negedge aclk);s_axis_tvalid=0;ctrl_word[9]=1;
            repeat(3)@(negedge aclk);
            ctrl_word[9]=0;
            repeat(2)@(negedge aclk);
            if(status_word[4:0]!=={2'b00,ctrl_word[12],2'b00})$fatal(1,"Soft reset status incorrect");
        end
    endtask

    integer widths[0:6];
    integer test,old_white,old_packets,k;
    reg [31:0] old_geom;
    initial begin
        widths[0]=1;widths[1]=2;widths[2]=3;widths[3]=4;widths[4]=5;widths[5]=9;widths[6]=2533;
        repeat(4)@(negedge aclk);aresetn=1;
        for(test=0;test<7;test=test+1)begin
            cfg_write(0,(4<<16)|widths[test]);
            set_roi(0,0,0,widths[test],4);
            set_roi(1,0,1,(widths[test]+1)/2,3);
            cfg_write(1,3);
            set_mode(120,0,1,1);frame(widths[test]*4,0,0);
            set_mode(120,0,1,0);frame(widths[test]*4,1,1);
            set_mode(120,1,1,1);frame(widths[test]*4,0,1);
        end
        
        cfg_write(0,(7<<16)|9);
        for(k=0;k<N;k=k+1)set_roi(k,k%7,k%5,k%7+3,k%5+2);
        cfg_write(1,(1<<N)-1);
        set_mode(120,0,1,1);frame(63,1,0);
        set_mode(120,0,0,1);frame(63,1,1);
        cfg_write(1,0);set_mode(120,0,1,1);frame(63,0,0);
        set_mode(255,0,0,0);frame(63,0,0);
        set_mode(0,0,0,0);frame(63,0,0);

        
        cfg_write(0,(1<<16)|4);set_roi(0,0,0,4,1);cfg_write(1,1);
        set_mode(120,0,1,1);drain();old_white=last_white;old_packets=expected_completed;
        ready_mode=0;@(negedge aclk);
        beat(32'hff878600,4'hf,1);
        repeat(5)@(negedge aclk);
        if(!m_axis_tvalid || status_word[31:16]!==old_packets || cfg_result_word!==old_white)$fatal(1,"LAST snapshot not held");
        ready_mode=1;drain();check_stats();

        
        @(negedge aclk);s_axis_tvalid=1;s_axis_tkeep=15;s_axis_tlast=1;s_axis_tdata=32'h01020304;
        repeat(5)begin @(posedge aclk);while(!s_axis_tready)@(posedge aclk);@(negedge aclk);s_axis_tdata=s_axis_tdata+32'h10101010;end
        s_axis_tvalid=0;drain();check_stats();
        ready_mode=2;

        
        cfg_write(0,(2<<16)|4);set_mode(120,0,1,0);
        frame(4,0,0);if(!status_word[4])$fatal(1,"Early LAST not detected");
        frame(12,0,0);if(!status_word[4])$fatal(1,"Extra pixels not detected");
        frame(8,0,0);if(status_word[4])$fatal(1,"Legal frame retains stale error");
        cfg_write(0,(2<<16));frame(4,0,0);if(!status_word[4])$fatal(1,"Zero width not detected");

        
        cfg_write(0,(2<<16)|4);set_mode(120,0,1,0);
        beat(32'h01020304,15,0);
        @(negedge aclk);cfg_data_word=(1<<16)|1;ctrl_word[23:16]=0;ctrl_word[12]=!ctrl_word[12];
        repeat(4)@(negedge aclk);
        if(!status_word[3] || status_word[2]!==ctrl_word[12] || cfg_result_word!==((2<<16)|4))$fatal(1,"Busy config not rejected");
        beat(32'h05060708,15,1);drain();soft_reset();
        ctrl_word[23:16]=0;#1;if(cfg_result_word!==((2<<16)|4))$fatal(1,"Soft reset changed geometry");

        
        set_mode(120,0,1,0);ready_mode=0;@(negedge aclk);
        beat(32'h01020304,15,0);soft_reset();
        if(m_axis_tvalid)$fatal(1,"Reset did not discard pending beat");
        ready_mode=2;frame(8,1,0);

        
        drain();ready_mode=1;
        fork
            begin cfg_write(0,(1<<16)|4); end
            begin
                repeat(2)@(negedge aclk);
                beat(32'hff878600,15,1);
            end
        join
        drain();check_stats();

        
        
        cfg_write(0,(2<<16)|4);set_mode(120,0,1,0);ready_mode=0;
        beat(32'h01020304,15,0);
        ctrl_word[9]=1;repeat(3)@(negedge aclk);ctrl_word[9]=0;repeat(2)@(negedge aclk);
        ready_mode=2;frame(8,0,0);
        
        ready_mode=0;beat(32'h01020304,15,0);soft_reset();
        ready_mode=2;frame(8,0,0);
        
        ready_mode=0;beat(32'h01020304,15,0);
        while(!m_axis_tvalid)@(negedge aclk);
        repeat(3)@(negedge aclk);soft_reset();
        if(m_axis_tvalid)$fatal(1,"Pipeline OUT reset left valid data");
        ready_mode=2;frame(8,0,0);
        
        cfg_write(0,(1<<16)|4);set_mode(120,0,1,0);ready_mode=0;
        beat(32'hff878600,15,1);
        cfg_data_word=(1<<16)|1;ctrl_word[23:16]=0;ctrl_word[12]=!ctrl_word[12];
        repeat(4)@(negedge aclk);
        if(!status_word[3] || status_word[2]!==ctrl_word[12] || cfg_result_word!==((1<<16)|4))
            $fatal(1,"Single-beat internal pipeline allowed configuration");
        ready_mode=1;drain();check_stats();soft_reset();
        
        cfg_write(0,(2<<16)|4);set_mode(120,0,1,0);
        @(negedge aclk);cfg_data_word=(1<<16)|1;ctrl_word[23:16]=0;ctrl_word[12]=!ctrl_word[12];
        @(posedge aclk);#2;ctrl_word[9]=1;
        repeat(3)@(negedge aclk);ctrl_word[9]=0;repeat(2)@(negedge aclk);
        if(cfg_result_word!==((2<<16)|4) || status_word[2]!==ctrl_word[12] || status_word[3])
            $fatal(1,"Pending reset did not preserve geometry/drop request");
        ready_mode=2;set_mode(120,0,1,0);frame(8,0,0);
        
        @(negedge aclk);ctrl_word[23:16]=2;cfg_data_word=32'hbad;ctrl_word[12]=!ctrl_word[12];
        repeat(5)@(negedge aclk);
        if(!status_word[3] || status_word[2]!==ctrl_word[12])$fatal(1,"Readonly write not rejected");
        soft_reset();
        if(back_to_back==0 || output_stalls==0 || input_stalls==0 || zero_keep==0)$fatal(1,"Required coverage missing");
        $display("PASS: ROI_PIPELINE_REVIEW packets=%0d input_beats=%0d output_beats=%0d input_stalls=%0d output_stalls=%0d back_to_back=%0d zero_keep=%0d",total_packets,total_input,total_output,input_stalls,output_stalls,back_to_back,zero_keep);
        $finish;
    end
    initial begin #5000000;$fatal(1,"Global timeout");end
endmodule
