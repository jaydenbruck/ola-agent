"""Generate the dependency-free, single-target Xcode project deterministically."""
from pathlib import Path

root = Path(__file__).parent
sources = sorted(path.name for path in (root / "Ola").glob("*.swift"))
def ident(number):
    return f"{number:024X}"

objects = []
def obj(number, value):
    objects.append(f"\t\t{ident(number)} = {{ {value} }};")

build_files = []
source_refs = []
for index, name in enumerate(sources):
    ref, build = 100 + index, 200 + index
    obj(ref, f'isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "{name}"; sourceTree = "<group>";')
    obj(build, f"isa = PBXBuildFile; fileRef = {ident(ref)};")
    source_refs.append(ident(ref))
    build_files.append(ident(build))
obj(1, f'isa = PBXProject; attributes = {{ LastUpgradeCheck = 1600; }}; buildConfigurationList = {ident(10)}; compatibilityVersion = "Xcode 14.0"; developmentRegion = de; hasScannedForEncodings = 0; knownRegions = (de, en, Base); mainGroup = {ident(2)}; productRefGroup = {ident(4)}; projectDirPath = ""; projectRoot = ""; targets = ({ident(5)});')
obj(2, f'isa = PBXGroup; children = ({ident(3)}, {ident(4)}); sourceTree = "<group>";')
obj(3, f'isa = PBXGroup; children = ({", ".join(source_refs)}, {ident(20)}, {ident(25)}); path = Ola; sourceTree = "<group>";')
obj(4, f'isa = PBXGroup; children = ({ident(6)}); name = Products; sourceTree = "<group>";')
obj(5, f'isa = PBXNativeTarget; buildConfigurationList = {ident(11)}; buildPhases = ({ident(7)}, {ident(8)}, {ident(9)}); buildRules = (); dependencies = (); name = Ola; productName = Ola; productReference = {ident(6)}; productType = "com.apple.product-type.application";')
obj(6, 'isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = Ola.app; sourceTree = BUILT_PRODUCTS_DIR;')
obj(7, f'isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = ({", ".join(build_files)}); runOnlyForDeploymentPostprocessing = 0;')
obj(8, 'isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0;')
obj(9, f'isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = ({ident(23)}, {ident(26)}); runOnlyForDeploymentPostprocessing = 0;')
obj(10, f'isa = XCConfigurationList; buildConfigurations = ({ident(12)}, {ident(13)}); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
obj(11, f'isa = XCConfigurationList; buildConfigurations = ({ident(14)}, {ident(15)}); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
common = 'CLANG_ENABLE_MODULES = YES; CLANG_ENABLE_OBJC_ARC = YES; IPHONEOS_DEPLOYMENT_TARGET = 17.0; SDKROOT = iphoneos; SWIFT_VERSION = 5.0;'
target = 'ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon; CODE_SIGN_STYLE = Automatic; DEVELOPMENT_TEAM = KKW3BWLT63; CURRENT_PROJECT_VERSION = 2; MARKETING_VERSION = 1.0; GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = Ola/Info.plist; PRODUCT_BUNDLE_IDENTIFIER = ai.tryola.agent; PRODUCT_NAME = "$(TARGET_NAME)"; TARGETED_DEVICE_FAMILY = 1; SUPPORTED_PLATFORMS = "iphoneos iphonesimulator"; SUPPORTS_MACCATALYST = NO; SWIFT_EMIT_LOC_STRINGS = YES;'
obj(12, f'isa = XCBuildConfiguration; buildSettings = {{ {common} DEBUG_INFORMATION_FORMAT = dwarf; SWIFT_OPTIMIZATION_LEVEL = "-Onone"; SWIFT_ACTIVE_COMPILATION_CONDITIONS = DEBUG; }}; name = Debug;')
obj(13, f'isa = XCBuildConfiguration; buildSettings = {{ {common} DEBUG_INFORMATION_FORMAT = "dwarf-with-dsym"; SWIFT_COMPILATION_MODE = wholemodule; SWIFT_OPTIMIZATION_LEVEL = "-O"; }}; name = Release;')
obj(14, f'isa = XCBuildConfiguration; buildSettings = {{ {target} }}; name = Debug;')
obj(15, f'isa = XCBuildConfiguration; buildSettings = {{ {target} }}; name = Release;')
obj(20, 'isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = Info.plist; sourceTree = "<group>";')
# Localized permission prompts are bundled as one variant group.
obj(21, 'isa = PBXFileReference; lastKnownFileType = text.plist.strings; name = de; path = de.lproj/InfoPlist.strings; sourceTree = "<group>";')
obj(22, 'isa = PBXFileReference; lastKnownFileType = text.plist.strings; name = en; path = en.lproj/InfoPlist.strings; sourceTree = "<group>";')
obj(23, f'isa = PBXBuildFile; fileRef = {ident(24)};')
obj(24, f'isa = PBXVariantGroup; children = ({ident(21)}, {ident(22)}); name = InfoPlist.strings; sourceTree = "<group>";')
obj(25, 'isa = PBXFileReference; lastKnownFileType = folder.assetcatalog; path = Assets.xcassets; sourceTree = "<group>";')
obj(26, f'isa = PBXBuildFile; fileRef = {ident(25)};')
objects[2 * len(sources) + 2] = objects[2 * len(sources) + 2].replace(f'{ident(20)},', f'{ident(20)}, {ident(24)},')
project = root / "Ola.xcodeproj"
project.mkdir(exist_ok=True)
(project / "project.pbxproj").write_text('// !$*UTF8*$!\n{\n\tarchiveVersion = 1;\n\tclasses = {};\n\tobjectVersion = 56;\n\tobjects = {\n' + '\n'.join(objects) + f'\n\t}};\n\trootObject = {ident(1)};\n}}\n', encoding="utf-8", newline="\n")
schemes = project / "xcshareddata" / "xcschemes"
schemes.mkdir(parents=True, exist_ok=True)
reference = f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{ident(5)}" BuildableName="Ola.app" BlueprintName="Ola" ReferencedContainer="container:Ola.xcodeproj"/>'
(schemes / "Ola.xcscheme").write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="1600" version="1.3">
<BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{reference}</BuildActionEntry></BuildActionEntries></BuildAction>
<TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables/></TestAction>
<LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{reference}</BuildableProductRunnable></LaunchAction>
<ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0">{reference}</BuildableProductRunnable></ProfileAction>
<AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
''', encoding="utf-8", newline="\n")
print(f"Generated Ola.xcodeproj with {len(sources)} Swift sources and one app target.")
