import archinstall
from archinstall.lib.disk import Disk
import os
import subprocess

# ==================== 配置参数（请根据需求修改）====================
CONFIG = {
    # 基本系统设置
    "language": "en_US",
    "locale": "en_US.UTF-8",
    "keyboard_layout": "us",  # 中文键盘用 "cn"
    "timezone": "Asia/Shanghai",
    "hostname": "myarch",
    
    # 磁盘配置（将由用户选择）
    "disk": None,            # 将在运行时由用户选择
    "filesystem": "btrfs",   # 使用 btrfs 文件系统
    "efi_size": "512M",      # EFI 分区大小
    "swap_size": "8G",       # 交换分区大小（0 表示不创建）
    
    # 用户设置
    "root_password": "root123",  # 安装后建议立即修改
    "username": "user",
    "user_password": "user123",  # 安装后建议立即修改
    
    # 软件包与桌面环境
    "additional_packages": [
        "base-devel", "linux-firmware", "networkmanager",
        "gnome", "gnome-extra", "gdm",  # GNOME 桌面
        "vim", "git", "firefox",
        # limine 引导程序
        "limine",
        # btrfs 工具
        "btrfs-progs",
        # snapper 快照工具
        "snapper", "grub-btrfs",  # grub-btrfs 用于生成快照启动项
        # 其他系统工具
        "efibootmgr"
    ],
    
    # 引导程序
    "bootloader": "limine"
}

def list_available_disks():
    """列出系统中所有可用的磁盘设备"""
    try:
        # 使用 lsblk 命令获取磁盘信息
        result = subprocess.run(
            ["lsblk", "-d", "-o", "NAME,SIZE,TYPE,MODEL", "--nodeps"],
            capture_output=True, text=True, check=True
        )
        
        # 解析输出，过滤出磁盘设备
        disks = []
        lines = result.stdout.strip().split('\n')[1:]  # 跳过标题行
        
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 3 and parts[2] == "disk":
                device = f"/dev/{parts[0]}"
                size = parts[1]
                model = ' '.join(parts[3:]) if len(parts) > 3 else "Unknown"
                disks.append({
                    "device": device,
                    "size": size,
                    "model": model
                })
        
        return disks
    except Exception as e:
        print(f"获取磁盘列表时出错: {str(e)}")
        return []

def select_disk():
    """让用户从可用磁盘列表中选择一个"""
    disks = list_available_disks()
    
    if not disks:
        print("未检测到可用磁盘设备")
        return None
    
    print("\n检测到以下磁盘设备:")
    for i, disk in enumerate(disks, 1):
        print(f"{i}. {disk['device']} - {disk['size']} - {disk['model']}")
    
    while True:
        try:
            choice = input("\n请输入要使用的磁盘编号 (1-{}): ".format(len(disks)))
            index = int(choice) - 1
            
            if 0 <= index < len(disks):
                selected = disks[index]["device"]
                print(f"你选择了: {selected}")
                
                # 确认警告
                confirm = input(f"警告: 此操作将格式化 {selected} 并删除所有数据！确认继续? [y/N] ")
                if confirm.lower() == 'y':
                    return selected
                else:
                    print("请重新重新选择磁盘")
            else:
                print(f"请输入有效的编号 (1-{})".format(len(disks)))
        except ValueError:
            print("请输入有效的数字")

def configure_snapper(installer):
    """配置 snapper 进行 btrfs 快照管理"""
    # 在 chroot 环境中执行命令配置 snapper
    installer.chroot("snapper --no-dbus -c root create-config /")
    
    # 配置 snapper 权限
    installer.chroot("groupadd -r snapper")
    installer.chroot(f"gpasswd -a {CONFIG['username']} snapper")
    
    # 配置 /etc/snapper/configs/root
    config_content = """
ALLOW_GROUPS="snapper"
BACKGROUND_COMPRESSION="zstd"
EMPTY_PRE_POST_ROLLBACK="no"
EMPTY_PRE_POST_SNAPSHOT="no"
FREE_LIMIT="0.2"
FSTYPE="btrfs"
NUMBER_CLEANUP="yes"
NUMBER_LIMIT="50"
NUMBER_LIMIT_IMPORTANT="10"
NUMBER_MIN_AGE="1800"
QGROUP=""
SPACE_LIMIT="0.5"
SUBVOLUME="/@"
SYNC_ACL="yes"
TIMELINE_CLEANUP="yes"
TIMELINE_LIMIT_DAILY="7"
TIMELINE_LIMIT_HOURLY="12"
TIMELINE_LIMIT_MONTHLY="1"
TIMELINE_LIMIT_WEEKLY="4"
TIMELINE_LIMIT_YEARLY="0"
TIMELINE_MIN_AGE="1800"
"""
    with open(installer.target + "/etc/snapper/configs/root", "w") as f:
        f.write(config_content.strip())
    
    # 启用并启动 snapper 服务
    installer.enable_service("snapper-timeline.timer")
    installer.enable_service("snapper-cleanup.timer")

def configure_limine(installer):
    """配置 limine 引导程序以支持 btrfs 和快照恢复，包括 NVRAM 条目"""
    # 找到 EFI 分区和磁盘
    efi_partition = next(p for p in installer.partitions if p.mountpoint == "/boot")
    efi_disk = efi_partition.device.path  # 磁盘设备（如 /dev/nvme0n1）
    efi_part_num = efi_partition.number   # 分区号（如 1）
    
    # 修复：使用磁盘设备而非分区路径安装 Limine
    # limine-install 需要的是磁盘路径，而不是分区路径
    installer.chroot(f"limine-install {efi_disk}")
    
    # 创建 EFI 目录并复制引导文件
    installer.chroot("mkdir -p /boot/EFI/limine")
    installer.chroot("cp /usr/share/limine/limine-uefi-cd.bin /boot/EFI/limine/limine.efi")
    installer.chroot("cp /boot/limine.cfg /boot/EFI/limine/")
    
    # 使用 efibootmgr 在 NVRAM 中添加 Limine 引导条目
    boot_entry_command = (
        f"efibootmgr --create "
        f"--disk {efi_disk} "
        f"--part {efi_part_num} "
        f"--loader /EFI/limine/limine.efi "
        f"--label \"Limine Bootloader\" "
        f"--unicode"
    )
    installer.chroot(boot_entry_command)
    
    # 设置为默认启动项
    installer.chroot("efibootmgr > /tmp/efibootmgr.txt")
    with open(installer.target + "/tmp/efibootmgr.txt", "r") as f:
        output = f.read()
    
    for line in output.splitlines():
        if "Limine Bootloader" in line:
            boot_num = line.split()[0].replace("Boot", "").replace("*", "").strip()
            installer.chroot(f"efibootmgr --bootorder {boot_num}")
            break
    
    # 创建 limine 配置文件
    limine_config = f"""
TIMEOUT=5
DEFAULT_ENTRY=0

:Arch Linux
    PROTOCOL=linux
    PATH={installer.root.subvolume}
    CMDLINE=root={installer.disk.device} rootflags=subvol=@ rw

:Arch Linux (fallback initramfs)
    PROTOCOL=linux
    PATH={installer.root.subvolume}
    CMDLINE=root={installer.disk.device} rootflags=subvol=@ rw
    INITRD=/boot/initramfs-linux-fallback.img

:Snapshot Boot Menu
    PROTOCOL=grub-btrfs
    PATH=boot/grub
"""
    with open(installer.target + "/boot/limine.cfg", "w") as f:
        f.write(limine_config.strip())
    
    # 更新 initramfs 以确保 btrfs 支持
    installer.chroot("mkinitcpio -P")

def main():
    try:
        # 让用户选择磁盘
        CONFIG["disk"] = select_disk()
        if not CONFIG["disk"]:
            print("未选择有效磁盘，安装终止")
            return
        
        # 验证磁盘是否存在
        disk = Disk(CONFIG["disk"])
        if not disk.exists:
            raise Exception(f"磁盘 {CONFIG['disk']} 不存在，请检查设备名称")

        # 创建安装配置
        with archinstall.Installer(
            disk,
            hostname=CONFIG["hostname"],
            locale=CONFIG["locale"],
            timezone=CONFIG["timezone"],
            keyboard_layout=CONFIG["keyboard_layout"]
        ) as installer:
            
            # 设置引导程序
            installer.set_bootloader(CONFIG["bootloader"])
            
            # Btrfs 分区方案（带子卷）
            partitions = [
                # EFI 分区
                {
                    "mountpoint": "/boot",
                    "filesystem": "vfat",
                    "size": CONFIG["efi_size"],
                    "flags": ["boot", "esp"]
                },
                # 交换分区（如果需要）
                *([{
                    "filesystem": "swap",
                    "size": CONFIG["swap_size"],
                    "mountpoint": None
                }] if CONFIG["swap_size"] != "0" else []),
                # Btrfs 主分区（包含子卷）
                {
                    "mountpoint": "/",
                    "filesystem": {
                        "format": CONFIG["filesystem"],
                        "mount_options": ["compress=zstd", "subvol=@"]
                    },
                    "size": "remaining",
                    # Btrfs 子卷配置
                    "subvolumes": [
                        {"name": "@", "mountpoint": "/"},
                        {"name": "@home", "mountpoint": "/home"},
                        {"name": "@var", "mountpoint": "/var"},
                        {"name": "@tmp", "mountpoint": "/tmp"},
                        {"name": "@snapshots", "mountpoint": "/.snapshots"}
                    ]
                }
            ]
            
            # 应用分区方案
            installer.partition_disk(partitions)
            
            # 设置 root 密码
            installer.user_set_password("root", CONFIG["root_password"])
            
            # 创建普通用户并赋予 sudo 权限
            installer.create_user(
                CONFIG["username"],
                CONFIG["user_password"],
                is_admin=True  # 给予 sudo 权限
            )
            
            # 安装额外软件包
            installer.add_additional_packages(CONFIG["additional_packages"])
            
            # 启用必要服务
            installer.enable_service("NetworkManager")  # 网络管理
            installer.enable_service("gdm")            # 显示管理器（如使用 GNOME）
            
            # 配置 snapper 快照
            configure_snapper(installer)
            
            # 配置 limine 引导程序（包含 NVRAM 条目）
            configure_limine(installer)
            
            # 执行安装
            print("开始安装 Arch Linux...")
            installer.install()
            
            print("安装完成！请重启系统")
            print("快照恢复说明:")
            print("1. 创建快照: snapper create --description 'before-update'")
            print("2. 列出快照: snapper list")
            print("3. 恢复快照: snapper rollback <快照编号>")
            print("引导管理:")
            print("查看启动项: efibootmgr")
            print("修改启动顺序: efibootmgr --bootorder XXXX,YYYY")

    except Exception as e:
        print(f"安装过程出错: {str(e)}")
        exit(1)

if __name__ == "__main__":
    main()
    
