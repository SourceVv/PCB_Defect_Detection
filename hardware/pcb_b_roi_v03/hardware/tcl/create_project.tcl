set script_dir [file dirname [file normalize [info script]]]
set root [file normalize [file join $script_dir ../..]]
set project_dir [file join $root build pcb_roi]
set rtl_file [file join $root hardware rtl axis_roi_threshold_stats.v]
if {[file exists $project_dir]} {
    error "Refusing to overwrite $project_dir. Use the existing project or a fresh package folder."
}
if {![file isfile $rtl_file]} {
    error "Missing ROI statistics RTL: $rtl_file. Copy the complete ROI package before creating the project."
}
set board_repo [file join $root board_files]
if {[info exists ::env(PYNQ_BOARD_REPO)]} {set board_repo $::env(PYNQ_BOARD_REPO)}
if {[file isdirectory $board_repo]} {
    set repo_paths [get_param board.repoPaths]
    if {[lsearch -exact $repo_paths $board_repo] < 0} {
        lappend repo_paths $board_repo
        set_param board.repoPaths $repo_paths
    }
}
set candidates [lsort -dictionary [get_board_parts -quiet *pynq-z2*]]
if {[llength $candidates] == 0} {
    error "PYNQ-Z2 board files are missing. Install the official TUL board files, then retry."
}
set board_part [lindex $candidates end]
puts "Using board preset: $board_part"
create_project pcb_roi $project_dir -part xc7z020clg400-1
set_property board_part $board_part [current_project]
set_property target_language Verilog [current_project]
add_files -norecurse $rtl_file
update_compile_order -fileset sources_1
create_bd_design pcb_roi_system

set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1"} $ps
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {64} \
    CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_RST0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100.000000}] $ps
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list CONFIG.c_include_sg {0} \
    CONFIG.c_micro_dma {0} CONFIG.c_prmry_is_aclk_async {0} \
    CONFIG.c_sg_include_stscntrl_strm {0} CONFIG.c_addr_width {32} \
    CONFIG.c_include_mm2s {1} CONFIG.c_include_s2mm {1} \
    CONFIG.c_include_mm2s_dre {0} CONFIG.c_include_s2mm_dre {0} \
    CONFIG.c_sg_length_width {23} \
    CONFIG.c_mm2s_burst_size {16} CONFIG.c_s2mm_burst_size {16} \
    CONFIG.c_m_axi_mm2s_data_width {64} CONFIG.c_m_axi_s2mm_data_width {64} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32}] $dma
set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_ctrl]
set_property -dict [list CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {0} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_DOUT_DEFAULT {0x00000180} CONFIG.C_TRI_DEFAULT {0x00000000} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1} CONFIG.C_ALL_OUTPUTS_2 {0} \
    CONFIG.C_DOUT_DEFAULT_2 {0x00000000} CONFIG.C_TRI_DEFAULT_2 {0xFFFFFFFF} \
    CONFIG.C_INTERRUPT_PRESENT {0}] $gpio
set cfg [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_cfg]
set_property -dict [list CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {0} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_DOUT_DEFAULT {0x00000000} CONFIG.C_TRI_DEFAULT {0x00000000} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1} CONFIG.C_ALL_OUTPUTS_2 {0} \
    CONFIG.C_DOUT_DEFAULT_2 {0x00000000} CONFIG.C_TRI_DEFAULT_2 {0xFFFFFFFF} \
    CONFIG.C_INTERRUPT_PRESENT {0}] $cfg

set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ctrl]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {3}] $ctrl
set mem [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_mem]
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] $mem
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_100m]
set_property -dict [list CONFIG.C_EXT_RESET_HIGH {0} CONFIG.C_AUX_RESET_HIGH {1}] $rst
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_zero]
set_property CONFIG.CONST_VAL {0} $zero
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_one]
set_property CONFIG.CONST_VAL {1} $one
create_bd_cell -type module -reference axis_roi_threshold_stats axis_roi_threshold_stats_0
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins axi_ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ctrl/M00_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins axi_ctrl/M01_AXI] [get_bd_intf_pins axi_gpio_ctrl/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ctrl/M02_AXI] [get_bd_intf_pins axi_gpio_cfg/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins axi_mem/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins axi_mem/s01_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_mem/M00_AXI] [get_bd_intf_pins ps7/S_AXI_HP0]

connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins axis_roi_threshold_stats_0/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins axis_roi_threshold_stats_0/M_AXIS] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]
connect_bd_net [get_bd_pins axi_gpio_ctrl/gpio_io_o] [get_bd_pins axis_roi_threshold_stats_0/ctrl_word]
connect_bd_net [get_bd_pins axis_roi_threshold_stats_0/status_word] [get_bd_pins axi_gpio_ctrl/gpio2_io_i]
connect_bd_net [get_bd_pins axi_gpio_cfg/gpio_io_o] [get_bd_pins axis_roi_threshold_stats_0/cfg_data_word]
connect_bd_net [get_bd_pins axis_roi_threshold_stats_0/cfg_result_word] [get_bd_pins axi_gpio_cfg/gpio2_io_i]
foreach pin {ps7/M_AXI_GP0_ACLK ps7/S_AXI_HP0_ACLK rst_100m/slowest_sync_clk \
    axi_dma_0/s_axi_lite_aclk axi_dma_0/m_axi_mm2s_aclk axi_dma_0/m_axi_s2mm_aclk \
    axi_gpio_ctrl/s_axi_aclk axi_gpio_cfg/s_axi_aclk \
    axi_ctrl/ACLK axi_ctrl/S00_ACLK axi_ctrl/M00_ACLK axi_ctrl/M01_ACLK axi_ctrl/M02_ACLK \
    axi_mem/ACLK axi_mem/S00_ACLK axi_mem/s01_ACLK axi_mem/M00_ACLK \
    axis_roi_threshold_stats_0/aclk} {
    connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins $pin]
}
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins rst_100m/ext_reset_in]
connect_bd_net [get_bd_pins const_zero/dout] [get_bd_pins rst_100m/aux_reset_in] [get_bd_pins rst_100m/mb_debug_sys_rst]
connect_bd_net [get_bd_pins const_one/dout] [get_bd_pins rst_100m/dcm_locked]
foreach pin {axi_ctrl/ARESETN axi_mem/ARESETN} {
    connect_bd_net [get_bd_pins rst_100m/interconnect_aresetn] [get_bd_pins $pin]
}
foreach pin {axi_ctrl/S00_ARESETN axi_ctrl/M00_ARESETN axi_ctrl/M01_ARESETN axi_ctrl/M02_ARESETN \
    axi_mem/S00_ARESETN axi_mem/s01_ARESETN axi_mem/M00_ARESETN \
    axi_dma_0/axi_resetn axi_gpio_ctrl/s_axi_aresetn axi_gpio_cfg/s_axi_aresetn axis_roi_threshold_stats_0/aresetn} {
    connect_bd_net [get_bd_pins rst_100m/peripheral_aresetn] [get_bd_pins $pin]
}
assign_bd_address
validate_bd_design
save_bd_design
set bd_file [get_files */pcb_roi_system.bd]
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper
set_property top pcb_roi_system_wrapper [current_fileset]
update_compile_order -fileset sources_1
source [file join $script_dir export_design_metadata.tcl]
export_roi_metadata [file join $project_dir reports creation]
puts "CREATED: [file join $project_dir pcb_roi.xpr]"
puts "No synthesis, implementation or board validation was performed by this script."
puts "Review the BD and reports, then run hardware/tcl/build_overlay.tcl."
