import os
import sys
from huggingface_hub import HfApi

def upload_to_hf():
    # 填入您刚刚在 Hugging Face 创建的仓库名称
    repo_id = "jamesphotography/SuperPicky-models"
    
    # 自动获取您电脑上的对应文件路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, ".."))
    
    files_to_upload = [
        {"local_path": "models/model20240824.pth", "repo_path": "model20240824.pth"},
        {"local_path": "models/superFlier_efficientnet.pth", "repo_path": "superFlier_efficientnet.pth"},
        {"local_path": "models/cub200_keypoint_resnet50_slim.pth", "repo_path": "cub200_keypoint_resnet50_slim.pth"}
    ]
    
    # 初始化 API
    api = HfApi()

    print(f"准备上传文件到仓库: {repo_id}")
    verify_token()

    for item in files_to_upload:
        full_local_path = os.path.join(project_root, item["local_path"])
        
        if not os.path.exists(full_local_path):
            print(f"❌ 找不到本地文件: {full_local_path}")
            continue
            
        repo_path = item["repo_path"]
        print(f"上传 {repo_path} 中 (文件大小: {os.path.getsize(full_local_path) / (1024*1024):.2f} MB)...")
        
        try:
            # 开始上传
            api.upload_file(
                path_or_fileobj=full_local_path,
                path_in_repo=repo_path,
                repo_id=repo_id,
                repo_type="model"
            )
            print(f"✅ 成功! {repo_path} 已上传。")
        except Exception as e:
            print(f"❌ 上传失败: {e}")

def verify_token():
    from huggingface_hub import whoami
    try:
        user_info = whoami()
        print(f"已使用账号登录: {user_info['name']}")
    except Exception:
        print("\n您尚未配置 Hugging Face 的访问令牌 (Access Token)。")
        print("因为这是您的仓库，上传必须需要权限验证。请按以下步骤操作：")
        print("1. 去 https://huggingface.co/settings/tokens 获取/创建一个 Token (权限需要选 'Write')")
        print("2. 在终端运行: huggingface-cli login (并粘贴刚才的 Token)")
        print("3. 登录成功后，重新运行 python scripts/upload_to_hf.py 此程序。")
        sys.exit(1)

if __name__ == "__main__":
    upload_to_hf()
