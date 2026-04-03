#!/bin/bash

# Loop over all .sz files in the current directory
for sz_file in *.sz; do
    # Extract the base name without the extension
    base_name="${sz_file%.sz}"

    # Construct paths to the associated NODDI images
    od_image="od/${base_name/_desc-preproc_dwi/_model-noddi_param-od_dwimap}.nii.gz"
    isovf_image="isovf/${base_name/_desc-preproc_dwi/_model-noddi_param-isovf_dwimap}.nii.gz"
    icvf_image="icvf/${base_name/_desc-preproc_dwi/_model-noddi_param-icvf_dwimap}.nii.gz"

    # Run dsi_studio command
    ../dsi-studio/dsi_studio --action=rec --source="$sz_file" \
        --method=7 \
        --param0=1.25 \
        --volume_correction=1 \
        --check_btable=1 \
        --motion_correction=1 \
	--other_output=all \
        --other_image=od:"$od_image",isovf:"$isovf_image",icvf:"$icvf_image"

done
