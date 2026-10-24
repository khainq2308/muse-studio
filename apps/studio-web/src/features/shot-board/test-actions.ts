import { screen, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";

/** Opens the collapsed generation setup (if needed) and chooses a connection. */
export async function chooseConnection(user: UserEvent, connectionId: string) {
  if (!screen.queryByLabelText("Connection")) {
    const setup = await screen.findByTestId("generation-setup");
    await user.click(within(setup).getByRole("button", { name: /^(Set up generation|Change)$/ }));
  }
  await user.selectOptions(await screen.findByLabelText("Connection"), connectionId);
}
