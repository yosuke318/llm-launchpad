output "instance_id" {
  description = "インスタンスID"
  value       = aws_instance.this.id
}

output "availability_zone" {
  description = "起動したAZ"
  value       = local.availability_zone
}

output "public_ip" {
  description = "パブリックIP"
  value       = aws_instance.this.public_ip
}

output "ami_id" {
  description = "使った AMI のID"
  value       = data.aws_ami.dlami.id
}

output "ami_name" {
  description = "使った AMI の名前"
  value       = data.aws_ami.dlami.name
}

output "root_volume_size_gib" {
  description = "ルートボリュームの大きさ(GiB)"
  value       = local.root_volume_size_gib
}

output "spot_options" {
  description = "スポットの設定(オンデマンドでは null)"
  value       = try(aws_instance.this.instance_market_options[0].spot_options[0], null)
}

output "spot_instance_request_id" {
  description = "スポットリクエストのID(オンデマンドでは空)"
  value       = aws_instance.this.spot_instance_request_id
}
