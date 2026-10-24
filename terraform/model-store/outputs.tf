output "bucket_name" {
  description = "S3キャッシュ用のバケット名。環境側では、同じ規則の名前なら指定しなくてよい"
  value       = local.bucket_name
}

output "model_volumes" {
  description = "EBS永続用のボリューム(ボリューム名ごとの ID と AZ)"
  value = { for k, v in aws_ebs_volume.models : k => {
    id                = v.id
    availability_zone = v.availability_zone
    size_gib          = v.size
  } }
}
