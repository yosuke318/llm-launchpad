output "file_system_id" {
  description = "S3 Files のファイルシステムの ID(mount -t s3files <ID>:/ に使う)"
  value       = aws_s3files_file_system.this.id
}

output "mount_target_ids" {
  description = "AZ ごとのマウントターゲット"
  value       = { for k, v in aws_s3files_mount_target.this : k => v.id }
}

output "mount_target_security_group_id" {
  description = "マウントターゲットのセキュリティグループ"
  value       = aws_security_group.mount_target.id
}
