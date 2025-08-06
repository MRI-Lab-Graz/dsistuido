#!/usr/bin/env python3
"""
DSI Studio Setup Validation Script

Quick validation script to check if DSI Studio and configuration are working properly.
Run this before processing large batches of data.

Usage: python validate_setup.py [--config connectivity_config.json]
"""

import sys
import json
import argparse
from pathlib import Path
from extract_connectivity_matrices import ConnectivityExtractor, DEFAULT_CONFIG


def main():
    parser = argparse.ArgumentParser(description="Validate DSI Studio setup and configuration")
    parser.add_argument('--config', type=str, help='Configuration file to validate')
    parser.add_argument('--input-folder', type=str, help='Test input folder for file discovery')
    
    args = parser.parse_args()
    
    # Load configuration
    config = DEFAULT_CONFIG.copy()
    config_file = args.config or 'connectivity_config.json'
    
    if Path(config_file).exists():
        print(f"📄 Loading configuration from: {config_file}")
        try:
            with open(config_file, 'r') as f:
                file_config = json.load(f)
                config.update(file_config)
            print("✅ Configuration loaded successfully")
        except Exception as e:
            print(f"❌ Error loading configuration: {e}")
            sys.exit(1)
    else:
        print(f"⚠️  Configuration file not found: {config_file}")
        print("   Using default configuration")
    
    # Override input folder if provided
    if args.input_folder:
        config.setdefault('input_settings', {})['input_folder'] = args.input_folder
    
    print("\n" + "="*60)
    print("🔍 DSI STUDIO SETUP VALIDATION")
    print("="*60)
    
    # Create extractor and run validation
    extractor = ConnectivityExtractor(config)
    validation_result = extractor.validate_configuration()
    
    print("\n" + "="*60)
    print("📊 VALIDATION SUMMARY")
    print("="*60)
    
    if validation_result['valid']:
        print("✅ VALIDATION PASSED - Ready for processing!")
        print(f"\n📋 Configuration Summary:")
        print(f"   🧠 DSI Studio: {config['dsi_studio_cmd']}")
        print(f"   🏗️  Atlases: {len(config['atlases'])} configured")
        print(f"   📊 Metrics: {len(config['connectivity_values'])} configured")
        print(f"   🔄 Tracks: {config['track_count']:,}")
        print(f"   ⚡ Threads: {config['thread_count']}")
        
        # Show input folder info if configured
        input_settings = config.get('input_settings', {})
        input_folder = input_settings.get('input_folder')
        if input_folder and input_folder != '/path/to/your/fib/files':
            print(f"   📁 Input folder: {input_folder}")
        
    else:
        print("❌ VALIDATION FAILED - Fix errors before processing!")
        return 1
    
    if validation_result['warnings']:
        print(f"\n⚠️  {len(validation_result['warnings'])} WARNINGS:")
        for warning in validation_result['warnings']:
            print(f"   ⚠️  {warning}")
    
    if validation_result['info']:
        print(f"\n💡 Additional Information:")
        for info in validation_result['info'][:3]:  # Show first 3 info items
            print(f"   ℹ️  {info}")
    
    print("\n" + "="*60)
    print("🚀 Next Steps:")
    print("="*60)
    
    if validation_result['valid']:
        print("1. ✅ Configuration is valid")
        print("2. 🧪 Try pilot mode first: --pilot --pilot-count 1")
        print("3. 📊 Run full batch processing")
        print("\nExample commands:")
        print(f"   python extract_connectivity_matrices.py --config {config_file} --pilot input.fib.gz output/")
        print(f"   python extract_connectivity_matrices.py --config {config_file} --batch input_dir/ output/")
    else:
        print("1. ❌ Fix the configuration errors above")
        print("2. 🔄 Re-run this validation script")
        print("3. 📚 Check the documentation for help")
    
    return 0 if validation_result['valid'] else 1


if __name__ == '__main__':
    sys.exit(main())
