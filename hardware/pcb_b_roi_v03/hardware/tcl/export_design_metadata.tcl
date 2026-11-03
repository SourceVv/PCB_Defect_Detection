proc export_roi_metadata {destination} {
    file mkdir $destination
    set version_file [open [file join $destination tool_versions.txt] w]
    fconfigure $version_file -encoding utf-8
    puts $version_file "ROI_STATS_ABI=0x03"
    puts $version_file "GENERATED_UTC=[clock format [clock seconds] -gmt 1 -format {%Y-%m-%dT%H:%M:%SZ}]"
    puts $version_file [version]
    puts $version_file "PART=[get_property PART [current_project]]"
    puts $version_file "BOARD_PART=[get_property BOARD_PART [current_project]]"
    puts $version_file "TOP=[get_property TOP [current_fileset]]"
    puts $version_file "BD_IP_INSTANCES:"
    foreach cell [lsort [get_bd_cells -hierarchical -filter {TYPE == ip}]] {
        puts $version_file "$cell\t[get_property VLNV $cell]"
    }
    close $version_file
    report_ip_status -file [file join $destination ip_status.rpt]
    set address_file [open [file join $destination address_map.tsv] w]
    fconfigure $address_file -encoding utf-8
    puts $address_file "address_space\tsegment\toffset\trange"
    foreach space [lsort [get_bd_addr_spaces -quiet]] {
        foreach segment [lsort [get_bd_addr_segs -quiet -of_objects $space]] {
            puts $address_file "$space\t$segment\t[get_property OFFSET $segment]\t[get_property RANGE $segment]"
        }
    }
    close $address_file
    report_property -file [file join $destination dma_properties.rpt] [get_bd_cells axi_dma_0]
    report_property -file [file join $destination gpio_properties.rpt] [get_bd_cells axi_gpio_ctrl]
    report_property -file [file join $destination cfg_gpio_properties.rpt] [get_bd_cells axi_gpio_cfg]
    report_property -file [file join $destination ps_properties.rpt] [get_bd_cells ps7]
}
