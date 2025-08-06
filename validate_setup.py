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
    parser = argparse.ArgumentParser(
        description="🔍 DSI Studio Setup Validation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
🎯 PURPOSE: Validate your DSI Studio installation and configuration before processing data

📋 EXAMPLES:

  # Basic validation (uses connectivity_config.json if available)
  python validate_setup.py
  
  # Validate specific configuration
  python validate_setup.py --config my_config.json
  
  # Test with actual data path
  python validate_setup.py --config my_config.json --test-input /path/to/data/
  
  # Test single file
  python validate_setup.py --test-input subject.fz
  
  # Test with different file pattern
  python validate_setup.py --test-input /data/dir/ --pattern "*.fz"

✅ WHAT IT CHECKS:
  - DSI Studio installation and accessibility
  - Configuration file validity
  - Atlas and metric specifications
  - Parameter ranges and values
  - Input path accessibility and file discovery
  - File format support (.fib.gz and .fz)

💡 RECOMMENDED WORKFLOW:
  1. Run validation first
  2. Fix any errors found
  3. Test with pilot mode
  4. Run full processing

For more help: see README.md
        """)
    
    parser.add_argument('--config', type=str, 
                       help='📄 JSON configuration file to validate (default: connectivity_config.json)')
    parser.add_argument('--test-input', type=str, 
                       help='🧪 Test input path: file or directory to validate')
    parser.add_argument('--pattern', type=str, default='*.fib.gz', 
                       help='🔍 File pattern for directory testing (default: *.fib.gz)')
    
    args = parser.parse_args()
    
    # Show help if no arguments provided
    if len(sys.argv) == 1:
        parser.print_help()
        print("\n💡 QUICK START: python validate_setup.py --config example_config.json")
        sys.exit(0)
    
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
    
    print("\n" + "="*60)
    print("🔍 DSI STUDIO SETUP VALIDATION")
    print("="*60)
    
    # Create extractor and run validation
    extractor = ConnectivityExtractor(config)
    validation_result = extractor.validate_configuration()
    
    # Test input path if provided
    if args.test_input:
        print(f"\n🔍 Testing input path: {args.test_input}")
        input_validation = extractor.validate_input_path(args.test_input, args.pattern)
        
        if input_validation['valid']:
            files_count = len(input_validation['files_found'])
            print(f"✅ Input validation passed - Found {files_count} file(s)")
        else:
            print("❌ Input validation failed:")
            for error in input_validation['errors']:
                print(f"   ❌ {error}")
            validation_result['valid'] = False
            validation_result['errors'].extend(input_validation['errors'])
    
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
        
        # Show test input info if provided
        if args.test_input:
            print(f"   🧪 Test input: {args.test_input}")
        
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
