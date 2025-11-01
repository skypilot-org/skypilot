import {
  // HomeLayout as BasicHomeLayout,
  // Layout as BasicLayout,
  getCustomMDXComponent as basicGetCustomMDXComponent,
} from '@rspress/core/theme';
// import { useI18n } from '@rspress/core/runtime';
import {
  LlmsContainer,
  LlmsCopyButton,
  LlmsViewOptions,
} from '@rspress/plugin-llms/runtime';

// function HomeLayout() {
//   const { pre: PreWithCodeButtonGroup, code: Code } =
//     basicGetCustomMDXComponent();
//   const t = useI18n();

//   return (
//     <BasicHomeLayout
//       afterHeroActions={
//         <div
//           className="rspress-doc"
//           style={{ minHeight: 'auto', width: '100%', maxWidth: 400 }}
//         >
//           <PreWithCodeButtonGroup
//             containerElementClassName="language-bash"
//             codeButtonGroupProps={{
//               showCodeWrapButton: false,
//             }}
//           >
//             <Code className="language-bash" style={{ textAlign: 'center' }}>
//               {t('oneLineCommand')}
//             </Code>
//           </PreWithCodeButtonGroup>
//         </div>
//       }
//     />
//   );
// }

function getCustomMDXComponent() {
  const { h1: H1, ...mdxComponents } = basicGetCustomMDXComponent();

  const MyH1 = ({ ...props }) => {
    return (
      <>
        <H1 {...props} />
        <LlmsContainer>
          <LlmsCopyButton />
          {/* LlmsViewOptions 组件可根据需要添加  */}
          <LlmsViewOptions />
        </LlmsContainer>
      </>
    );
  };
  return {
    ...mdxComponents,
    h1: MyH1,
  };
}

export { getCustomMDXComponent };
export * from '@rspress/core/theme';
