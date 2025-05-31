# deployment.py

import os
import subprocess

class DeploymentManager:
    def __init__(self, project_id, region, image_name):
        self.project_id = project_id
        self.region = region
        self.image = image_name

    def build_and_push(self):
        print("[部署] 建立並上傳映像至 GCP...")
        cmd = f"gcloud builds submit --tag gcr.io/{self.project_id}/{self.image}"
        subprocess.run(cmd, shell=True, check=True)

    def deploy_to_cloud_run(self, service_name="trade-bot"):
        print("[部署] 部署至 Cloud Run...")
        cmd = (
            f"gcloud run deploy {service_name} \
             --image gcr.io/{self.project_id}/{self.image} \
             --platform managed \
             --region {self.region} \
             --allow-unauthenticated"
        )
        subprocess.run(cmd, shell=True, check=True)

    def deploy(self):
        self.build_and_push()
        self.deploy_to_cloud_run()

if __name__ == "__main__":
    from config import CONFIG
    manager = DeploymentManager(
        CONFIG["cloud"]["project_id"],
        CONFIG["cloud"]["region"],
        CONFIG["cloud"]["image"].split("/")[-1]
    )
    manager.deploy()