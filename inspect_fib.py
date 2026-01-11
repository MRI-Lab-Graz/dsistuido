import scipy.io
import gzip
import sys

def inspect_fib(file_path):
    print(f"Inspecting {file_path}")
    try:
        with gzip.open(file_path, 'rb') as f:
            mat = scipy.io.loadmat(f)
            print("Keys in MAT file:")
            for key in mat.keys():
                if not key.startswith('__'):
                    print(f"  {key}: {mat[key].shape if hasattr(mat[key], 'shape') else type(mat[key])}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_fib(sys.argv[1])
