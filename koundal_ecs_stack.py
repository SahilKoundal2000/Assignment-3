from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_elasticloadbalancingv2 as elbv2,
    aws_ecs_patterns as ecs_patterns,
    aws_codebuild as codebuild,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_iam as iam,
    core
)

class KoundalEcsStack(core.Stack):

    def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # Create VPC with public and private subnets
        vpc = ec2.Vpc(
            self, "KoundalVpc",
            max_azs=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE,
                    cidr_mask=24
                )
            ]
        )

        # Create ECR repository
        ecr_repo = ecr.Repository(
            self, "KoundalEcrRepo",
            repository_name="koundal-flask-app",
            image_scan_on_push=True
        )

        # Create ECS Cluster
        cluster = ecs.Cluster(
            self, "KoundalEcsCluster",
            vpc=vpc,
            cluster_name="koundal-flask-cluster"
        )

        # Create Fargate Service with Application Load Balancer
        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "KoundalFargateService",
            cluster=cluster,
            memory_limit_mib=512,
            cpu=256,
            task_image_options={
                "image": ecs.ContainerImage.from_registry("amazon/amazon-ecs-sample"),
                "container_port": 80
            },
            desired_count=2,
            public_load_balancer=True
        )

        # Auto Scaling
        scalable_target = fargate_service.service.auto_scale_task_count(
            min_capacity=2,
            max_capacity=4
        )
        scalable_target.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70
        )

        # Security Group for ALB
        alb_sg = ec2.SecurityGroup(
            self, "KoundalAlbSg",
            vpc=vpc,
            description="Allow HTTP traffic to ALB",
            allow_all_outbound=True
        )
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow HTTP from anywhere"
        )

        # Security Group for ECS Tasks
        ecs_sg = ec2.SecurityGroup(
            self, "KoundalEcsSg",
            vpc=vpc,
            description="Allow traffic only from ALB",
            allow_all_outbound=True
        )
        ecs_sg.add_ingress_rule(
            alb_sg,
            ec2.Port.tcp(80),
            "Allow HTTP from ALB"
        )

        # CodeBuild Project
        build_project = codebuild.PipelineProject(
            self, "KoundalBuildProject",
            project_name="koundal-flask-build",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_5_0,
                privileged=True
            ),
            environment_variables={
                "ECR_REPO_URI": codebuild.BuildEnvironmentVariable(
                    value=ecr_repo.repository_uri
                )
            },
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml")
        )

        # Grant permissions to CodeBuild
        ecr_repo.grant_pull_push(build_project)
        build_project.add_to_role_policy(iam.PolicyStatement(
            actions=["ecs:*"],
            resources=["*"]
        ))

        # CodePipeline
        source_output = codepipeline.Artifact()
        build_output = codepipeline.Artifact()

        pipeline = codepipeline.Pipeline(
            self, "KoundalPipeline",
            pipeline_name="koundal-flask-pipeline",
            stages=[
                codepipeline.StageProps(
                    stage_name="Source",
                    actions=[
                        codepipeline_actions.GitHubSourceAction(
                            action_name="GitHub_Source",
                            owner="YOUR_GITHUB_USERNAME",
                            repo="YOUR_REPO_NAME",
                            branch="main",
                            oauth_token=core.SecretValue.secrets_manager("github-token"),
                            output=source_output
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Build",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="Docker_Build",
                            project=build_project,
                            input=source_output,
                            outputs=[build_output]
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Deploy",
                    actions=[
                        codepipeline_actions.EcsDeployAction(
                            action_name="ECS_Deploy",
                            service=fargate_service.service,
                            image_file=build_output.at_path("imagedefinitions.json")
                        )
                    ]
                )
            ]
        )
