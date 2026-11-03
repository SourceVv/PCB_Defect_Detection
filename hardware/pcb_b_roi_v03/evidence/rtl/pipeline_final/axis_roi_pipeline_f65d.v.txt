`timescale 1ns / 1ps


module axis_roi_threshold_stats #(
    parameter integer ROI_COUNT = 17
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF S_AXIS:M_AXIS, ASSOCIATED_RESET aresetn, FREQ_HZ 100000000" *)
    input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    input wire [31:0] ctrl_word,
    output wire [31:0] status_word,
    input wire [31:0] cfg_data_word,
    output reg [31:0] cfg_result_word,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1, TUSER_WIDTH 0, TID_WIDTH 0, TDEST_WIDTH 0, FREQ_HZ 100000000" *)
    input wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *)
    input wire [3:0] s_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input wire s_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input wire s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1, TUSER_WIDTH 0, TID_WIDTH 0, TDEST_WIDTH 0, FREQ_HZ 100000000" *)
    output reg [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *)
    output reg [3:0] m_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output reg m_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output reg m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input wire m_axis_tready
);
    reg [15:0] cfg_width, cfg_height;
    reg [ROI_COUNT-1:0] cfg_enable;
    reg [15:0] cfg_x0 [0:ROI_COUNT-1];
    reg [15:0] cfg_y0 [0:ROI_COUNT-1];
    reg [15:0] cfg_x1 [0:ROI_COUNT-1];
    reg [15:0] cfg_y1 [0:ROI_COUNT-1];
    reg cfg_ack, cfg_error, cfg_pending, cfg_pending_token;
    reg [7:0] cfg_pending_address;
    wire [7:0] cfg_address = ctrl_word[23:16];
    wire cfg_request = ctrl_word[12] != cfg_ack;

    localparam [1:0] P_IDLE=0, P_COMPARE=1, P_ACCUM=2, P_OUT=3;
    reg [1:0] pipe_state;
    reg in_packet;
    reg [15:0] completed_packets;
    reg [7:0] packet_threshold;
    reg packet_bypass, packet_invert, packet_roi;
    reg [15:0] pixel_x;
    reg [16:0] pixel_y; 
    reg [31:0] work_area [0:ROI_COUNT-1];
    reg [31:0] committed_area [0:ROI_COUNT-1];
    reg [31:0] work_white, work_pixels;
    reg [31:0] committed_white, committed_pixels;
    reg work_error, frame_error;

    wire reset_active = !aresetn || ctrl_word[9];
    wire stream_busy = in_packet || m_axis_tvalid || (pipe_state != P_IDLE);
    wire busy = stream_busy || cfg_pending || cfg_request;
    
    assign s_axis_tready = !reset_active && !cfg_pending && !cfg_request &&
                          (pipe_state == P_IDLE || (pipe_state == P_OUT && m_axis_tvalid && m_axis_tready));
    assign status_word = {completed_packets, 8'h03, 3'b000,
                          frame_error, cfg_error, cfg_ack, in_packet, busy};

    wire [7:0] active_threshold = in_packet ? packet_threshold : ctrl_word[7:0];
    wire active_bypass = in_packet ? packet_bypass : ctrl_word[8];
    wire active_invert = in_packet ? packet_invert : ctrl_word[10];
    wire active_roi = in_packet ? packet_roi : ctrl_word[11];

    
    
    function [32:0] advance_position;
        input [15:0] x;
        input [16:0] y;
        input [2:0] n;
        input [15:0] width;
        input [15:0] height;
        reg [16:0] sum_x;
        reg [17:0] sum_y;
        reg [15:0] next_x;
        reg [2:0] rows;
        begin
            sum_x = {1'b0,x} + n;
            next_x = 0;
            rows = 0;
            case (width)
                0: begin next_x = 0; rows = 0; end
                1: begin next_x = 0; rows = n; end
                2: begin next_x = {15'b0,sum_x[0]}; rows = sum_x[3:1]; end
                3: begin
                    if (sum_x >= 6) begin next_x = sum_x - 6; rows = 2; end
                    else if (sum_x >= 3) begin next_x = sum_x - 3; rows = 1; end
                    else next_x = sum_x[15:0];
                end
                default: begin
                    if (sum_x >= {1'b0,width}) begin
                        next_x = sum_x - {1'b0,width}; rows = 1;
                    end else next_x = sum_x[15:0];
                end
            endcase
            sum_y = {1'b0,y} + rows;
            
            
            if (y >= {1'b0,height})
                advance_position = {{1'b0,height},16'b0};
            else
                advance_position = {sum_y[16:0],next_x};
        end
    endfunction

    integer cfg_i;
    integer cfg_index;
    always @(posedge aclk) begin
        if (!aresetn) begin
            cfg_width <= 0; cfg_height <= 0; cfg_enable <= 0;
            cfg_ack <= 0; cfg_error <= 0; cfg_pending <= 0;
            cfg_pending_token <= 0; cfg_pending_address <= 0;
            for (cfg_i=0; cfg_i<ROI_COUNT; cfg_i=cfg_i+1) begin
                cfg_x0[cfg_i] <= 0; cfg_y0[cfg_i] <= 0;
                cfg_x1[cfg_i] <= 0; cfg_y1[cfg_i] <= 0;
            end
        end else if (ctrl_word[9]) begin
            
            cfg_ack <= ctrl_word[12]; cfg_error <= 0; cfg_pending <= 0;
        end else if (cfg_pending) begin
            cfg_pending <= 0;
            cfg_ack <= cfg_pending_token;
            if (ctrl_word[12] != cfg_pending_token || cfg_address != cfg_pending_address) begin
                
                cfg_error <= 1;
                cfg_ack <= ctrl_word[12];
            end else if (cfg_pending_address == 8'h00) begin
                cfg_width <= cfg_data_word[15:0];
                cfg_height <= cfg_data_word[31:16];
            end else if (cfg_pending_address == 8'h01) begin
                cfg_enable <= cfg_data_word[ROI_COUNT-1:0];
            end else if (cfg_pending_address >= 8'h10 &&
                         cfg_pending_address < (8'h10 + 4*ROI_COUNT)) begin
                cfg_index = (cfg_pending_address - 8'h10) >> 2;
                case (cfg_pending_address[1:0])
                    0: cfg_x0[cfg_index] <= cfg_data_word[15:0];
                    1: cfg_y0[cfg_index] <= cfg_data_word[15:0];
                    2: cfg_x1[cfg_index] <= cfg_data_word[15:0];
                    3: cfg_y1[cfg_index] <= cfg_data_word[15:0];
                endcase
            end else cfg_error <= 1;
        end else if (cfg_request) begin
            if (stream_busy) begin
                cfg_error <= 1;
                cfg_ack <= ctrl_word[12];
            end else begin
                cfg_pending <= 1;
                cfg_pending_address <= cfg_address;
                cfg_pending_token <= ctrl_word[12];
            end
        end
    end

    integer read_index;
    always @* begin
        cfg_result_word = 0;
        read_index = 0;
        if (cfg_address == 0) cfg_result_word = {cfg_height,cfg_width};
        else if (cfg_address == 1) cfg_result_word = {{(32-ROI_COUNT){1'b0}},cfg_enable};
        else if (cfg_address == 2) cfg_result_word = committed_white;
        else if (cfg_address == 3) cfg_result_word = committed_pixels;
        else if (cfg_address >= 8'h10 && cfg_address < (8'h10+4*ROI_COUNT)) begin
            read_index = (cfg_address-8'h10) >> 2;
            case (cfg_address[1:0])
                0: cfg_result_word = {16'b0,cfg_x0[read_index]};
                1: cfg_result_word = {16'b0,cfg_y0[read_index]};
                2: cfg_result_word = {16'b0,cfg_x1[read_index]};
                3: cfg_result_word = {16'b0,cfg_y1[read_index]};
            endcase
        end else if (cfg_address >= 8'h80 && cfg_address < (8'h80+ROI_COUNT)) begin
            read_index = cfg_address-8'h80;
            cfg_result_word = committed_area[read_index];
        end
    end

    
    
    reg [32:0] incoming_position [0:3];
    reg [32:0] incoming_final_position;
    reg [2:0] incoming_count;
    reg incoming_error;
    integer position_lane;
    always @* begin
        incoming_count = 0;
        incoming_error = (cfg_width == 0 || cfg_height == 0);
        incoming_final_position = {pixel_y,pixel_x};
        for (position_lane=0; position_lane<4; position_lane=position_lane+1) begin
            incoming_position[position_lane] = advance_position(
                pixel_x,pixel_y,incoming_count,cfg_width,cfg_height);
            if (s_axis_tkeep[position_lane])
                incoming_count = incoming_count + 1'b1;
        end
        incoming_final_position = advance_position(
            pixel_x,pixel_y,incoming_count,cfg_width,cfg_height);
    end

    reg [31:0] beat_data;
    reg [3:0] beat_keep;
    reg beat_last, beat_first, beat_error;
    reg [2:0] beat_count;
    reg [7:0] beat_threshold;
    reg beat_bypass, beat_invert, beat_roi;
    reg [32:0] beat_position [0:3];
    reg [32:0] beat_final_position;

    
    
    reg [31:0] calculated_data;
    reg [2:0] calculated_area [0:ROI_COUNT-1];
    reg [2:0] calculated_white;
    reg calculated_position_error;
    reg [7:0] compared_pixel;
    reg foreground, hit, union_hit, pixel_in_frame;
    integer compare_lane, compare_roi;
    always @* begin
        calculated_data = 0;
        calculated_white = 0;
        calculated_position_error = beat_error;
        compared_pixel = 0;
        foreground = 0; hit = 0; union_hit = 0; pixel_in_frame = 0;
        for (compare_roi=0; compare_roi<ROI_COUNT; compare_roi=compare_roi+1)
            calculated_area[compare_roi] = 0;
        for (compare_lane=0; compare_lane<4; compare_lane=compare_lane+1) begin
            if (beat_keep[compare_lane]) begin
                if (beat_position[compare_lane][32:16] >= {1'b0,cfg_height})
                    calculated_position_error = 1;
                compared_pixel = beat_invert ? (8'hff-beat_data[compare_lane*8 +: 8]) :
                                               beat_data[compare_lane*8 +: 8];
                foreground = compared_pixel > beat_threshold;
                pixel_in_frame = cfg_width != 0 && cfg_height != 0 &&
                                 beat_position[compare_lane][32:16] < {1'b0,cfg_height};
                union_hit = 0;
                for (compare_roi=0; compare_roi<ROI_COUNT; compare_roi=compare_roi+1) begin
                    hit = cfg_enable[compare_roi] && pixel_in_frame &&
                          beat_position[compare_lane][15:0] >= cfg_x0[compare_roi] &&
                          beat_position[compare_lane][15:0] < cfg_x1[compare_roi] &&
                          beat_position[compare_lane][32:16] >= {1'b0,cfg_y0[compare_roi]} &&
                          beat_position[compare_lane][32:16] < {1'b0,cfg_y1[compare_roi]};
                    union_hit = union_hit || hit;
                    if (!beat_bypass && hit && foreground)
                        calculated_area[compare_roi] = calculated_area[compare_roi] + 1'b1;
                end
                if (beat_bypass)
                    calculated_data[compare_lane*8 +: 8] = beat_data[compare_lane*8 +: 8];
                else if (foreground && (!beat_roi || union_hit)) begin
                    calculated_data[compare_lane*8 +: 8] = 8'hff;
                    calculated_white = calculated_white + 1'b1;
                end
            end
        end
        if (beat_last && (beat_final_position[15:0] != 0 ||
            beat_final_position[32:16] != {1'b0,cfg_height}))
            calculated_position_error = 1;
    end

    reg [31:0] processed_data;
    reg [2:0] increment_area [0:ROI_COUNT-1];
    reg [2:0] increment_white;
    integer state_i;
    always @(posedge aclk) begin
        if (reset_active) begin
            pipe_state <= P_IDLE;
            m_axis_tdata <= 0; m_axis_tkeep <= 0; m_axis_tlast <= 0; m_axis_tvalid <= 0;
            in_packet <= 0; completed_packets <= 0;
            packet_threshold <= 128; packet_bypass <= 1; packet_invert <= 0; packet_roi <= 0;
            pixel_x <= 0; pixel_y <= 0;
            beat_data <= 0; beat_keep <= 0; beat_last <= 0; beat_first <= 0;
            beat_error <= 0; beat_count <= 0;
            beat_threshold <= 128; beat_bypass <= 1; beat_invert <= 0; beat_roi <= 0;
            beat_final_position <= 0;
            processed_data <= 0; increment_white <= 0;
            work_white <= 0; work_pixels <= 0; work_error <= 0;
            committed_white <= 0; committed_pixels <= 0; frame_error <= 0;
            for (state_i=0; state_i<4; state_i=state_i+1) beat_position[state_i] <= 0;
            for (state_i=0; state_i<ROI_COUNT; state_i=state_i+1) begin
                work_area[state_i] <= 0; committed_area[state_i] <= 0;
                increment_area[state_i] <= 0;
            end
        end else begin
            if (pipe_state == P_COMPARE) begin
                processed_data <= calculated_data;
                increment_white <= calculated_white;
                
                beat_error <= calculated_position_error;
                for (state_i=0; state_i<ROI_COUNT; state_i=state_i+1)
                    increment_area[state_i] <= calculated_area[state_i];
                pipe_state <= P_ACCUM;
            end
            if (pipe_state == P_ACCUM) begin
                
                work_white <= beat_first ? {29'b0,increment_white} : work_white + increment_white;
                work_pixels <= beat_first ? {29'b0,beat_count} : work_pixels + beat_count;
                work_error <= beat_first ? beat_error : (work_error || beat_error);
                for (state_i=0; state_i<ROI_COUNT; state_i=state_i+1)
                    work_area[state_i] <= beat_first ? {29'b0,increment_area[state_i]} :
                                                      work_area[state_i] + increment_area[state_i];
                m_axis_tdata <= processed_data; m_axis_tkeep <= beat_keep;
                m_axis_tlast <= beat_last; m_axis_tvalid <= 1;
                pipe_state <= P_OUT;
            end
            if (m_axis_tvalid && m_axis_tready) begin
                m_axis_tvalid <= 0;
                pipe_state <= P_IDLE;
                if (m_axis_tlast) begin
                    
                    completed_packets <= completed_packets + 1'b1;
                    committed_white <= work_white; committed_pixels <= work_pixels;
                    frame_error <= work_error;
                    for (state_i=0; state_i<ROI_COUNT; state_i=state_i+1)
                        committed_area[state_i] <= work_area[state_i];
                end
            end
            
            if (s_axis_tvalid && s_axis_tready) begin
                beat_data <= s_axis_tdata; beat_keep <= s_axis_tkeep; beat_last <= s_axis_tlast;
                beat_first <= !in_packet; beat_error <= incoming_error; beat_count <= incoming_count;
                beat_final_position <= incoming_final_position;
                beat_threshold <= active_threshold; beat_bypass <= active_bypass;
                beat_invert <= active_invert; beat_roi <= active_roi;
                for (state_i=0; state_i<4; state_i=state_i+1)
                    beat_position[state_i] <= incoming_position[state_i];
                if (!in_packet) begin
                    packet_threshold <= ctrl_word[7:0]; packet_bypass <= ctrl_word[8];
                    packet_invert <= ctrl_word[10]; packet_roi <= ctrl_word[11];
                end
                in_packet <= !s_axis_tlast;
                pixel_x <= s_axis_tlast ? 16'b0 : incoming_final_position[15:0];
                pixel_y <= s_axis_tlast ? 17'b0 : incoming_final_position[32:16];
                pipe_state <= P_COMPARE;
            end
        end
    end
endmodule

