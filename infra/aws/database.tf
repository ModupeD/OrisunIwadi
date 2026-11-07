# PostgreSQL on RDS, in subnets with no internet route. Encrypted, backed up daily (point-in-time
# recovery for db_backup_retention_days: 7 by default, 1 on the free plan), TLS required. With high_availability it is Multi-AZ: a standby in the
# other zone takes over automatically (about 1-2 minutes) if the primary or its zone fails.

locals {
  db_name       = "oncolake"
  db_admin_user = "oncolake_admin"
  # Passwords are not in these strings: each task gets PGPASSWORD from Secrets Manager.
  # verify-full checks the server's certificate against the RDS CA bundle baked into the image.
  dsn_base = join(" ", [
    "host=${aws_db_instance.main.address}",
    "port=${aws_db_instance.main.port}",
    "dbname=${local.db_name}",
    "sslmode=verify-full",
    "sslrootcert=/etc/ssl/certs/rds-global-bundle.pem",
    "connect_timeout=5",
  ])
}

resource "aws_db_subnet_group" "main" {
  name       = var.name
  subnet_ids = aws_subnet.database[*].id
}

resource "aws_db_parameter_group" "main" {
  name   = "${var.name}-postgres16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000" # log queries slower than 1 s
  }
  parameter {
    name  = "log_connections"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  db_name                     = local.db_name
  username                    = local.db_admin_user
  manage_master_user_password = true # RDS creates and stores it in Secrets Manager; never in state

  allocated_storage     = 20
  max_allocated_storage = 100 # grows by itself instead of filling up
  storage_type          = "gp3"
  storage_encrypted     = true

  multi_az               = var.high_availability
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  parameter_group_name   = aws_db_parameter_group.main.name

  backup_retention_period    = var.db_backup_retention_days
  backup_window              = "07:00-08:00"         # UTC, before the 6 AM Eastern load
  maintenance_window         = "sun:08:30-sun:09:30" # UTC
  auto_minor_version_upgrade = true
  copy_tags_to_snapshot      = true

  enabled_cloudwatch_logs_exports = ["postgresql"]

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = !var.deletion_protection
  final_snapshot_identifier = "${var.name}-final"
}
