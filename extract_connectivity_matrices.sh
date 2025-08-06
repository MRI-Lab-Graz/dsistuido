#!/bin/bash

# DSI Studio Connectivity Matrix Extraction Script
# Author: Generated for connectivity analysis
# Usage: ./extract_connectivity_matrices.sh [input_file.fib.gz] [output_directory]

# Default parameters - modify as needed (based on DSI Studio source code analysis)
DEFAULT_ATLASES="AAL,AAL2,AAL3,Brodmann,HCP-MMP,AICHA,Talairach,FreeSurferDKT,FreeSurferDKT_Cortical,Schaefer100,Schaefer200,Schaefer400,Gordon333,Power264"
DEFAULT_CONNECTIVITY_VALUES="count,ncount,ncount2,mean_length,qa,fa,dti_fa,md,ad,rd,iso,rdi,ndi"
DEFAULT_TRACK_COUNT=100000
DEFAULT_THREAD_COUNT=8
DEFAULT_METHOD=0
DEFAULT_TURNING_ANGLE=0
DEFAULT_STEP_SIZE=0
DEFAULT_SMOOTHING=0
DEFAULT_TRACK_VOXEL_RATIO=2.0

# Function to display usage
show_usage() {
    echo "Usage: $0 [OPTIONS] INPUT_FILE OUTPUT_DIR"
    echo ""
    echo "Extract connectivity matrices for different atlases using DSI Studio"
    echo ""
    echo "Arguments:"
    echo "  INPUT_FILE    Path to .fib.gz file"
    echo "  OUTPUT_DIR    Directory to save connectivity matrices"
    echo ""
    echo "Basic Options:"
    echo "  -a, --atlases         Comma-separated list of atlases (default: $DEFAULT_ATLASES)"
    echo "  -v, --values         Comma-separated list of connectivity values (default: $DEFAULT_CONNECTIVITY_VALUES)"
    echo "  -t, --tracks         Number of tracks to generate (default: $DEFAULT_TRACK_COUNT)"
    echo "  -j, --threads        Number of threads (default: $DEFAULT_THREAD_COUNT)"
    echo ""
    echo "Tracking Parameters:"
    echo "  --method            Tracking algorithm: 0=Streamline, 1=RK4, 2=Voxel (default: $DEFAULT_METHOD)"
    echo "  --turning_angle     Maximum turning angle in degrees (default: $DEFAULT_TURNING_ANGLE=random)"
    echo "  --step_size         Step size in mm (default: $DEFAULT_STEP_SIZE=random)"
    echo "  --smoothing         Smoothing fraction 0-1 (default: $DEFAULT_SMOOTHING)"
    echo "  --track_voxel_ratio Track-to-voxel ratio (default: $DEFAULT_TRACK_VOXEL_RATIO)"
    echo "  --fa_threshold      FA threshold (default: automatic)"
    echo "  --otsu_threshold    Otsu threshold (default: 0.6)"
    echo ""
    echo "  -h, --help           Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 subject001.fib.gz ./connectivity_output"
    echo "  $0 -a AAL2,HCP-MMP -v count,fa,ncount2 subject001.fib.gz ./output"
    echo "  $0 --method 1 --turning_angle 45 --tracks 50000 subject.fib.gz ./matrices"
    echo ""
    echo "Available Atlases: AAL, AAL2, AAL3, Brodmann, HCP-MMP, AICHA, Talairach,"
    echo "                   FreeSurferDKT, FreeSurferDKT_Cortical, Schaefer100/200/400,"
    echo "                   Gordon333, Power264"
    echo ""
    echo "Available Connectivity Values: count, ncount, ncount2, mean_length, qa, fa,"
    echo "                              dti_fa, md, ad, rd, iso, rdi, ndi, dti_ad, dti_rd, dti_md"
}

# Function to check if DSI Studio is available
check_dsi_studio() {
    if ! command -v dsi_studio &> /dev/null; then
        echo "Error: dsi_studio command not found. Please ensure DSI Studio is installed and in PATH."
        exit 1
    fi
}

# Function to validate input file
validate_input() {
    if [ ! -f "$1" ]; then
        echo "Error: Input file '$1' does not exist."
        exit 1
    fi
    
    if [[ ! "$1" =~ \.(fib\.gz|fz)$ ]]; then
        echo "Warning: Input file should be a .fib.gz or .fz file"
    fi
}

# Function to create output directory
create_output_dir() {
    if [ ! -d "$1" ]; then
        mkdir -p "$1"
        echo "Created output directory: $1"
    fi
}

# Function to extract connectivity matrices
extract_matrices() {
    local input_file="$1"
    local output_dir="$2"
    local atlases="$3"
    local values="$4"
    local track_count="$5"
    local thread_count="$6"
    
    echo "Starting connectivity matrix extraction..."
    echo "Input: $input_file"
    echo "Output directory: $output_dir"
    echo "Atlases: $atlases"
    echo "Connectivity values: $values"
    echo "Track count: $track_count"
    echo "Threads: $thread_count"
    echo ""
    
    # Get base filename without extension
    local base_name=$(basename "$input_file" .fib.gz)
    base_name=$(basename "$base_name" .fz)
    
    # Create timestamp for this run
    local timestamp=$(date +"%Y%m%d_%H%M%S")
    local run_dir="${output_dir}/${base_name}_${timestamp}"
    mkdir -p "$run_dir"
    
    # Loop through each atlas
    IFS=',' read -ra ATLAS_ARRAY <<< "$atlases"
    for atlas in "${ATLAS_ARRAY[@]}"; do
        echo "Processing atlas: $atlas"
        
        # Create atlas-specific directory
        local atlas_dir="${run_dir}/${atlas}"
        mkdir -p "$atlas_dir"
        
        # Extract connectivity matrix for current atlas
        local output_prefix="${atlas_dir}/${base_name}_${atlas}"
        
        dsi_studio --action=trk \
                  --source="$input_file" \
                  --tract_count="$track_count" \
                  --connectivity="$atlas" \
                  --connectivity_value="$values" \
                  --thread_count="$thread_count" \
                  --output="${output_prefix}.tt.gz" \
                  --export=stat
        
        if [ $? -eq 0 ]; then
            echo "✓ Successfully processed atlas: $atlas"
        else
            echo "✗ Failed to process atlas: $atlas"
        fi
        echo ""
    done
    
    echo "Connectivity matrix extraction completed!"
    echo "Results saved in: $run_dir"
}

# Main script logic
main() {
    # Default values
    local atlases="$DEFAULT_ATLASES"
    local values="$DEFAULT_CONNECTIVITY_VALUES"
    local track_count="$DEFAULT_TRACK_COUNT"
    local thread_count="$DEFAULT_THREAD_COUNT"
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -a|--atlases)
                atlases="$2"
                shift 2
                ;;
            -v|--values)
                values="$2"
                shift 2
                ;;
            -t|--tracks)
                track_count="$2"
                shift 2
                ;;
            -j|--threads)
                thread_count="$2"
                shift 2
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            -*|--*)
                echo "Unknown option $1"
                show_usage
                exit 1
                ;;
            *)
                break
                ;;
        esac
    done
    
    # Check required arguments
    if [ $# -ne 2 ]; then
        echo "Error: Missing required arguments"
        show_usage
        exit 1
    fi
    
    local input_file="$1"
    local output_dir="$2"
    
    # Validate environment and inputs
    check_dsi_studio
    validate_input "$input_file"
    create_output_dir "$output_dir"
    
    # Extract matrices
    extract_matrices "$input_file" "$output_dir" "$atlases" "$values" "$track_count" "$thread_count"
}

# Run main function with all arguments
main "$@"
