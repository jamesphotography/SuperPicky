import os
import sys
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="strict")

try:
    from huggingface_hub import hf_hub_download
except ImportError:
    print("Error: huggingface_hub is not installed. Please run `pip install huggingface_hub tqdm` first.")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)

# Define the models and their destination directories relative to the project root
MODELS_TO_DOWNLOAD = [
    {
        "category": "Classification",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "model20240824.pth",
        "dest_dir": "models",
    },
    {
        "category": "Flight Detection",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "superFlier_efficientnet.pth",
        "dest_dir": "models",
    },
    {
        "category": "Keypoint Detection",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "cub200_keypoint_resnet50_slim.pth",
        "dest_dir": "models",
    },
    {
        "category": "Database",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "avonet.db",
        "dest_dir": "birdid/data",
    },
    {
        "category": "Quality Assessment",
        "repo_id": "chaofengc/IQA-PyTorch-Weights",
        "filename": "cfanet_iaa_ava_res50-3cd62bb3.pth",
        "dest_dir": "models",
    }
]

def main():
    """
    Downloads required models and database files from Hugging Face Hub.
    Ensures files are placed in the correct directories for the application to function.
    """
    logging.info("Starting model download process...")
    
    # Ensure we're running from the project root (where this script is located in an expected directory)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, ".."))
    
    # Change to project root to simplify path handling if run from elsewhere
    os.chdir(project_root)
    logging.info(f"Working directory set to: {project_root}")

    success_count = 0
    total_models = len(MODELS_TO_DOWNLOAD)

    for item in MODELS_TO_DOWNLOAD:
        repo_id = item["repo_id"]
        filename = item["filename"]
        dest_dir = item["dest_dir"]
        category = item["category"]
        
        full_dest_dir = os.path.join(project_root, dest_dir)
        full_dest_path = os.path.join(full_dest_dir, filename)

        logging.info(f"[{category}] Retrieving {filename}...")
        
        # Ensure destination directory exists
        os.makedirs(full_dest_dir, exist_ok=True)
        
        try:
            # Download file using huggingface_hub. It handles caching automatically.
            # We use local_dir to bypass symlink behaviors and put it right where we want it.
            # If the file already exists and is the correct size/hash, it won't redownload.
            downloaded_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=full_dest_dir,
                local_dir_use_symlinks=False
            )
            logging.info(f"✓ Successfully downloaded/verified: {os.path.basename(downloaded_path)}")
            success_count += 1
        except Exception as e:
            logging.error(f"✗ Failed to download {filename} from {repo_id}: {str(e)}")

    if success_count == total_models:
        logging.info(f"All {total_models} files are ready.")
        logging.info("Application is ready to run.")
        sys.exit(0)
    else:
        logging.error(f"Only {success_count}/{total_models} files were successfully downloaded.")
        logging.error("Please check your internet connection and verify the files exist in the specified Hugging Face repositories.")
        sys.exit(1)

if __name__ == "__main__":
    main()
