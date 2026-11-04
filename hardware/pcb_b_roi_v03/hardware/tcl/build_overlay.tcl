set jobs 4
if {$argc > 1} {
    error "Usage: build_overlay.tcl -tclargs <jobs:1..16>"
}
if {$argc == 1} {
    set jobs [lindex $argv 0]
}
if {![string is integer -strict $jobs] || $jobs < 1 || $jobs > 16} {
    error "jobs must be an integer between 1 and 16"
}
set script_dir [file dirname [file normalize [info script]]]
set root [file normalize [file join $script_dir ../..]]
set project_dir [file join $root build pcb_roi]
set project_file [file join $project_dir pcb_roi.xpr]
set release [file join $root release roi_v0_3]
set report_dir [file join $root evidence vivado generated [format {%s_%s} [clock format [clock seconds] -format {%Y%m%d_%H%M%S}] [pid]]]
if {![file isfile $project_file]} {
    error "Missing project: $project_file. Run create_project.tcl first."
}
if {[file exists $release]} {
    error "Refusing to overwrite release directory: $release"
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
if {[llength [get_board_parts -quiet tul.com.tw:pynq-z2:part0:1.0]] != 1} {
    error "Required PYNQ-Z2 board preset is unavailable in this process"
}
open_project $project_file
set_property board_part tul.com.tw:pynq-z2:part0:1.0 [current_project]
launch_runs synth_1 -jobs $jobs
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "Synthesis did not finish: [get_property STATUS [get_runs synth_1]]"
}
launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "Implementation did not finish: [get_property STATUS [get_runs impl_1]]"
}
open_run impl_1
file mkdir $report_dir
report_utilization -file [file join $report_dir utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained \
    -max_paths 10 -file [file join $report_dir timing_summary.rpt]
report_drc -file [file join $report_dir drc.rpt]
report_methodology -file [file join $report_dir methodology.rpt]
report_route_status -file [file join $report_dir route_status.rpt]
check_timing -verbose -file [file join $report_dir check_timing.rpt]
report_clock_interaction -file [file join $report_dir clock_interaction.rpt]
set setup_paths [get_timing_paths -quiet -setup -max_paths 1]
set hold_paths [get_timing_paths -quiet -hold -max_paths 1]
if {[llength $setup_paths] == 0 || [llength $hold_paths] == 0} {
    error "Missing timing paths; review reports before publishing"
}
set routed_setup_wns [get_property SLACK [lindex $setup_paths 0]]
set routed_hold_whs [get_property SLACK [lindex $hold_paths 0]]
if {$routed_setup_wns < 0 || $routed_hold_whs < 0} {
    error "Routed timing failed; reports retained, no release bit/hwh copied"
}

set bit [file join $project_dir pcb_roi.runs impl_1 pcb_roi_system_wrapper.bit]
set hwh [file join $project_dir pcb_roi.gen sources_1 bd pcb_roi_system hw_handoff pcb_roi_system.hwh]
if {![file isfile $hwh]} {
    set hwh [file join $project_dir pcb_roi.srcs sources_1 bd pcb_roi_system hw_handoff pcb_roi_system.hwh]
}
if {![file isfile $bit] || ![file isfile $hwh]} {
    error "Missing matching bit/hwh output: bit=$bit hwh=$hwh"
}
file mkdir $release
foreach report [glob -nocomplain -directory $report_dir *.rpt] {
    file copy $report [file join $release [file tail $report]]
}
open_bd_design [get_files */pcb_roi_system.bd]
source [file join $script_dir export_design_metadata.tcl]
export_roi_metadata $release
write_bd_tcl -force [file join $release pcb_roi_system_rebuild.tcl]
file copy $bit [file join $release pcb_roi.bit]
file copy $hwh [file join $release pcb_roi.hwh]
set summary [open [file join $release timing_gate.txt] w]
puts $summary "ROUTED_SETUP_WNS_NS=$routed_setup_wns"
puts $summary "ROUTED_HOLD_WHS_NS=$routed_hold_whs"
puts $summary "REPORT_DIRECTORY=$report_dir"
puts $summary "TIMING_GATE=PASS"
puts $summary "BOARD_TEST=NOT_PERFORMED_BY_THIS_BUILD"
close $summary
puts "EXPORTED: $release"
puts "Review reports before use. Generated files do not establish on-board correctness."
